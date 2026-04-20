"""
CSI 矩陣相似度分析器。

負責：
- 建立與管理跌倒模板矩陣
- 計算目前 CSI 矩陣與模板矩陣的相關係數（相似度）
- 判斷是否超過相似度門檻

核心改進：
  以「逐子載波時序相關」取代「整矩陣展平相關」，
  聚焦在每個子載波的時間變化型態（穩定→突變→新穩態），
  而非被靜態振幅 profile 主導。

  每個子載波計算時間軸 Pearson r（取絕對值，不限方向），
  再對所有子載波取平均。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.65


def matrix_correlation(A: np.ndarray, B: np.ndarray) -> float:
    """計算兩個矩陣的平均絕對子載波時序 Pearson 相關係數。

    對每個子載波（row），分別計算時間軸上的 Pearson 相關係數，
    取絕對值後平均。這樣聚焦在「時序型態是否匹配」，
    而不受靜態振幅分佈影響。

    Parameters
    ----------
    A, B : np.ndarray
        shape (n_subcarriers, T)。

    Returns
    -------
    r : float
        平均絕對子載波時序相關係數，範圍 [0, 1]。
    """
    min_s = min(A.shape[0], B.shape[0])
    min_t = min(A.shape[1], B.shape[1])

    if min_t < 3 or min_s == 0:
        return 0.0

    abs_corrs = []
    for i in range(min_s):
        a = A[i, :min_t].astype(np.float64)
        b = B[i, :min_t].astype(np.float64)

        a_std = np.std(a)
        b_std = np.std(b)

        # 若任一子載波在時間軸上幾乎無變化，視為不相關
        if a_std < 1e-12 or b_std < 1e-12:
            abs_corrs.append(0.0)
            continue

        r = np.corrcoef(a, b)[0, 1]
        if np.isnan(r):
            abs_corrs.append(0.0)
        else:
            abs_corrs.append(abs(r))

    return float(np.mean(abs_corrs)) if abs_corrs else 0.0


class FallTemplateManager:
    """跌倒模板管理器。

    支援：
    - 從模擬資料自動建立預設模板
    - 從檔案載入/儲存模板
    - 管理多個模板並取最高相似度
    """

    def __init__(self):
        self._templates: list[np.ndarray] = []

    @property
    def template_count(self) -> int:
        return len(self._templates)

    def add_template(self, matrix: np.ndarray) -> None:
        """新增一個跌倒模板矩陣。"""
        self._templates.append(matrix.copy())

    def clear_templates(self) -> None:
        self._templates.clear()

    def save_templates(self, filepath: str) -> None:
        """將所有模板儲存為 .npz 檔。"""
        if not self._templates:
            logger.warning("No templates to save.")
            return
        np.savez(filepath, *[t for t in self._templates])
        logger.info("Saved %d templates to %s", len(self._templates), filepath)

    def load_templates(self, filepath: str) -> None:
        """從 .npz 檔載入模板。"""
        data = np.load(filepath)
        self._templates = [data[k] for k in data.files]
        logger.info("Loaded %d templates from %s", len(self._templates), filepath)

    def build_default_template(self, n_subcarriers: int = 52, window_size: int = 40) -> None:
        """建立預設跌倒模板（標準階梯函數）。

        反映跌倒時的 CSI 時序特徵：
        - 前 1/4：穩定基線
        - 中 1/4：線性轉換（跌倒瞬間）
        - 後 1/2：新穩態（人倒在地上）

        使用絕對子載波相關時，所有子載波往同一方向偏移即可，
        因為計算時取 |r|，不在意實際偏移方向。
        """
        template = np.zeros((n_subcarriers, window_size), dtype=np.float64)

        transition_start = window_size // 4
        transition_end = transition_start + window_size // 4

        base_val = 0.5
        shift_val = 0.3  # 統一偏移方向

        for i in range(n_subcarriers):
            # 穩定期
            template[i, :transition_start] = base_val

            # 轉換期（線性過渡）
            for t in range(transition_start, transition_end):
                progress = (t - transition_start) / max(transition_end - transition_start - 1, 1)
                template[i, t] = base_val + shift_val * progress

            # 新穩態
            template[i, transition_end:] = base_val + shift_val

        self._templates.append(template)
        logger.info("Built default fall template (step function): shape %s", template.shape)


class SimilarityAnalyzer:
    """CSI 矩陣相似度分析器。"""

    def __init__(
        self,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        template_manager: Optional[FallTemplateManager] = None,
    ):
        self.threshold = threshold
        self.template_manager = template_manager or FallTemplateManager()

        # 若沒有模板，建立預設模板
        if self.template_manager.template_count == 0:
            self.template_manager.build_default_template()

    def compute_similarity(self, current_matrix: np.ndarray) -> float:
        """計算目前矩陣與所有模板的最高相似度。

        Parameters
        ----------
        current_matrix : np.ndarray, shape (n_subcarriers, T)

        Returns
        -------
        max_similarity : float
            與所有模板中最高的相關係數。
        """
        if self.template_manager.template_count == 0:
            return 0.0

        max_sim = -1.0
        for template in self.template_manager._templates:
            # 對齊矩陣大小（取較小的時間維度）
            min_t = min(current_matrix.shape[1], template.shape[1])
            min_s = min(current_matrix.shape[0], template.shape[0])
            A = current_matrix[:min_s, :min_t]
            B = template[:min_s, :min_t]
            sim = matrix_correlation(A, B)
            if sim > max_sim:
                max_sim = sim

        return max_sim

    def check_similarity_threshold(self, similarity: float) -> bool:
        """判斷相似度是否超過門檻。"""
        return similarity > self.threshold
