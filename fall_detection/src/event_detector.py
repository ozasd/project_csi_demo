"""
事件偵測器。

負責：
- 整合 STI 與相似度分析結果
- 追蹤高相似度的持續時間
- 判定最終狀態：normal / motion_detected / fall_detected
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional

import numpy as np

from .sti_analyzer import STIAnalyzer, DEFAULT_STI_THRESHOLD
from .similarity_analyzer import SimilarityAnalyzer, DEFAULT_SIMILARITY_THRESHOLD


class DetectionStatus(str, Enum):
    NORMAL = "normal"
    MOTION_DETECTED = "motion_detected"
    FALL_DETECTED = "fall_detected"


class EventDetector:
    """跌倒事件偵測器，整合 STI + 相似度 + 振幅偏移 + 持續時間判斷。

    判斷流程（兩階段）：
    階段一（觸發）：
      1. STI > sti_threshold → 有動作
      2. 計算 CSI 矩陣與跌倒模板的時序相似度
      3. 相似度 > similarity_threshold → 疑似跌倒，開始觀察

    階段二（確認）：
      4. 觀察期間，計算振幅偏移量（當前平均振幅 vs 事件前基線）
      5. 若振幅持續偏移 > shift_threshold（人在地上，CSI 穩定在新水準）
         且持續 > duration_threshold → 判定跌倒
      6. 若振幅恢復到基線 → 取消觀察（只是走動）
    """

    # 振幅偏移門檻：相對於基線的比例
    _SHIFT_THRESHOLD = 0.08
    # 觀察寬限期（秒）：觸發後給予一段時間讓振幅偏移建立
    _GRACE_SECONDS = 2.0
    # STI 緩衝期（秒）：STI 觸發後持續計算相似度的時間
    # （解決滑動窗口對齊延遲問題）
    _STI_BUFFER_SECONDS = 3.0

    def __init__(
        self,
        sti_threshold: float = DEFAULT_STI_THRESHOLD,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        duration_threshold: float = 3.0,
        sti_analyzer: Optional[STIAnalyzer] = None,
        similarity_analyzer: Optional[SimilarityAnalyzer] = None,
        adl_similarity_analyzer: Optional[SimilarityAnalyzer] = None,
        similarity_margin: float = 0.0,
    ):
        self.sti_threshold = sti_threshold
        self.similarity_threshold = similarity_threshold
        self.duration_threshold = duration_threshold
        self.similarity_margin = similarity_margin

        self.sti_analyzer = sti_analyzer or STIAnalyzer(threshold=sti_threshold)
        self.similarity_analyzer = similarity_analyzer or SimilarityAnalyzer(
            threshold=similarity_threshold
        )
        self.adl_similarity_analyzer = adl_similarity_analyzer

        self._status = DetectionStatus.NORMAL
        self._high_similarity_start: Optional[float] = None
        self._current_sti: float = 0.0
        self._current_similarity: float = 0.0
        self._current_adl_similarity: float = 0.0
        self._fall_confirmed_at: Optional[float] = None
        self._last_timestamp: float = 0.0
        # 基線振幅追蹤（正常時期的平均振幅向量）
        self._baseline_amplitudes: Optional[np.ndarray] = None
        self._amplitude_shift: float = 0.0
        # STI 觸發追蹤
        self._last_sti_spike: float = 0.0

    def reset(self) -> None:
        """重置偵測器狀態。"""
        self.sti_analyzer.reset()
        self._status = DetectionStatus.NORMAL
        self._high_similarity_start = None
        self._current_sti = 0.0
        self._current_similarity = 0.0
        self._current_adl_similarity = 0.0
        self._fall_confirmed_at = None
        self._last_timestamp = 0.0
        self._baseline_amplitudes = None
        self._amplitude_shift = 0.0
        self._last_sti_spike = 0.0

    def update(
        self,
        subcarrier_amplitudes: np.ndarray,
        amplitude_matrix: Optional[np.ndarray] = None,
        timestamp: Optional[float] = None,
    ) -> DetectionStatus:
        """輸入最新一筆資料，更新偵測狀態。

        Parameters
        ----------
        subcarrier_amplitudes : np.ndarray, shape (n_subcarriers,)
            最新時間點的子載波振幅。
        amplitude_matrix : np.ndarray, shape (n_subcarriers, T), optional
            當前時間窗口的完整矩陣（用於相似度計算）。
            若未提供，則跳過相似度判斷。
        timestamp : float, optional
            當前時間戳。若未提供則使用 time.time()。

        Returns
        -------
        status : DetectionStatus
        """
        now = timestamp or time.time()
        self._last_timestamp = now

        # 1. 計算 STI
        self._current_sti = self.sti_analyzer.update(subcarrier_amplitudes)

        # 2. 追蹤 STI 觸發時間
        if self._current_sti > self.sti_threshold:
            self._last_sti_spike = now

        # STI 是否在緩衝期內（解決窗口對齊延遲：
        # STI 在跌倒瞬間飆高，但窗口要再滑幾步才包含完整轉換型態）
        sti_recently_active = (
            self._last_sti_spike > 0
            and (now - self._last_sti_spike) < self._STI_BUFFER_SECONDS
        )
        in_observation = self._high_similarity_start is not None

        # 3. 計算當前窗口平均振幅
        if amplitude_matrix is not None and amplitude_matrix.shape[1] >= 2:
            current_mean = np.mean(amplitude_matrix, axis=1)
        else:
            current_mean = subcarrier_amplitudes.copy()

        # 4. 只在完全安靜期間更新基線（非觀察中、STI 未觸發）
        if not in_observation and not sti_recently_active:
            if self._baseline_amplitudes is None:
                self._baseline_amplitudes = current_mean.copy()
            else:
                alpha = 0.1
                self._baseline_amplitudes = (
                    (1 - alpha) * self._baseline_amplitudes + alpha * current_mean
                )
            self._amplitude_shift = 0.0
            self._status = DetectionStatus.NORMAL
            return self._status

        # 5. 計算相對振幅偏移
        if self._baseline_amplitudes is not None:
            diff = current_mean - self._baseline_amplitudes
            baseline_norm = np.linalg.norm(self._baseline_amplitudes)
            if baseline_norm > 1e-12:
                self._amplitude_shift = float(np.linalg.norm(diff) / baseline_norm)
            else:
                self._amplitude_shift = 0.0

        # 5. 計算相似度（需要完整矩陣）
        if amplitude_matrix is not None and amplitude_matrix.shape[1] >= 2:
            self._current_similarity = self.similarity_analyzer.compute_similarity(
                amplitude_matrix
            )
            if self.adl_similarity_available:
                self._current_adl_similarity = self.adl_similarity_analyzer.compute_similarity(
                    amplitude_matrix
                )
            else:
                self._current_adl_similarity = 0.0
        else:
            if self._current_sti > self.sti_threshold:
                self._status = DetectionStatus.MOTION_DETECTED
            return self._status

        # 6. 判斷是否符合跌倒條件
        #    條件 A：相似度超標（時序型態與跌倒模板吻合）
        fall_pattern_detected = self._current_similarity > self.similarity_threshold
        if self.adl_similarity_available:
            fall_pattern_detected = (
                fall_pattern_detected
                and (self._current_similarity - self._current_adl_similarity) >= self.similarity_margin
            )

        #    條件 B：已在觀察期 + 振幅持續偏移（人在地上，CSI 穩定在新水準）
        post_fall_stable = (
            in_observation
            and self._amplitude_shift > self._SHIFT_THRESHOLD
            and self._current_sti <= self.sti_threshold
        )

        # 寬限期：觸發後短時間內無條件維持觀察
        # （橋接相似度下降到振幅偏移建立之間的空窗期）
        in_grace = (
            in_observation
            and (now - self._high_similarity_start) < self._GRACE_SECONDS
        )

        if fall_pattern_detected or post_fall_stable or in_grace:
            # 開始或繼續觀察計時
            if self._high_similarity_start is None:
                self._high_similarity_start = now

            elapsed = now - self._high_similarity_start

            if elapsed >= self.duration_threshold:
                self._status = DetectionStatus.FALL_DETECTED
                if self._fall_confirmed_at is None:
                    self._fall_confirmed_at = now
            else:
                self._status = DetectionStatus.MOTION_DETECTED
        else:
            # 不符合條件 → 取消觀察
            self._high_similarity_start = None
            if self._current_sti > self.sti_threshold:
                self._status = DetectionStatus.MOTION_DETECTED
            else:
                self._status = DetectionStatus.NORMAL

        return self._status

    @property
    def status(self) -> DetectionStatus:
        return self._status

    @property
    def current_sti(self) -> float:
        return self._current_sti

    @property
    def current_similarity(self) -> float:
        return self._current_similarity

    @property
    def similarity_available(self) -> bool:
        return self.similarity_analyzer.has_templates

    @property
    def adl_similarity_available(self) -> bool:
        return (
            self.adl_similarity_analyzer is not None
            and self.adl_similarity_analyzer.has_templates
        )

    @property
    def current_adl_similarity(self) -> float:
        return self._current_adl_similarity

    @property
    def amplitude_shift(self) -> float:
        """當前振幅相對於基線的偏移量（比例）。"""
        return self._amplitude_shift

    @property
    def high_similarity_duration(self) -> float:
        """目前高相似度已持續的秒數（基於取樣時間戳）。"""
        if self._high_similarity_start is None:
            return 0.0
        return self._last_timestamp - self._high_similarity_start

    @property
    def fall_confirmed_at(self) -> Optional[float]:
        return self._fall_confirmed_at

    def get_state_dict(self, timestamp: Optional[float] = None) -> dict:
        """取得目前偵測狀態的字典表示。"""
        from datetime import datetime, timezone

        ts = timestamp or time.time()
        return {
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "sti": round(self._current_sti, 4),
            "similarity": round(self._current_similarity, 4),
            "adl_similarity": round(self._current_adl_similarity, 4),
            "similarity_gap": round(self._current_similarity - self._current_adl_similarity, 4),
            "status": self._status.value,
            "high_similarity_duration": round(self.high_similarity_duration, 2),
        }
