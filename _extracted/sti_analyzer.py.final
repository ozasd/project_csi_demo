"""
STI（Signal Tendency Index）分析器。

負責：
- 計算每個時間點所有子載波的平均值
- 計算標準化向量
- 計算相鄰時間點的 STI
- 判斷是否超過門檻

STI 公式：
  H^t = [H_1^t, ..., H_n^t]
  H_bar^t = mean(H^t)
  H'^t = (H^t - H_bar^t) / std(H^t)
  STI_t = ||H'^t - H'^(t-1)||
"""

from __future__ import annotations

import numpy as np

# 預設門檻
DEFAULT_STI_THRESHOLD = 0.22


def _standardize_vector(v: np.ndarray) -> np.ndarray:
    """對向量做零均值、單位標準差標準化。"""
    mean = np.mean(v)
    std = np.std(v)
    if std < 1e-12:
        return np.zeros_like(v)
    return (v - mean) / std


def compute_sti_series(amplitude_matrix: np.ndarray) -> np.ndarray:
    """計算整段時間的 STI 序列。

    Parameters
    ----------
    amplitude_matrix : np.ndarray, shape (n_subcarriers, T)
        前處理後的振幅矩陣。

    Returns
    -------
    sti_values : np.ndarray, shape (T,)
        每個時間點的 STI 值（已正規化）。第一個時間點為 0。
    """
    n_sub, T = amplitude_matrix.shape
    if T == 0:
        return np.empty(0)

    sti_values = np.zeros(T, dtype=np.float64)
    norm_factor = np.sqrt(n_sub) if n_sub > 0 else 1.0

    prev_std = _standardize_vector(amplitude_matrix[:, 0])
    for t in range(1, T):
        curr_std = _standardize_vector(amplitude_matrix[:, t])
        diff = curr_std - prev_std
        sti_values[t] = np.linalg.norm(diff) / norm_factor
        prev_std = curr_std

    return sti_values


def compute_sti_current(amplitude_matrix: np.ndarray) -> float:
    """計算最新時間點的 STI 值。

    Parameters
    ----------
    amplitude_matrix : np.ndarray, shape (n_subcarriers, T)
        至少需要 2 個時間點。

    Returns
    -------
    sti : float
        最新的 STI 值（已正規化）。若資料不足則回傳 0.0。
    """
    if amplitude_matrix.shape[1] < 2:
        return 0.0

    n_sub = amplitude_matrix.shape[0]
    norm_factor = np.sqrt(n_sub) if n_sub > 0 else 1.0
    prev_std = _standardize_vector(amplitude_matrix[:, -2])
    curr_std = _standardize_vector(amplitude_matrix[:, -1])
    return float(np.linalg.norm(curr_std - prev_std) / norm_factor)


def check_sti_threshold(sti_value: float, threshold: float = DEFAULT_STI_THRESHOLD) -> bool:
    """判斷 STI 是否超過門檻。"""
    return sti_value > threshold


class STIAnalyzer:
    """STI 分析器，維護滑動窗口的 STI 狀態。"""

    def __init__(self, threshold: float = DEFAULT_STI_THRESHOLD):
        self.threshold = threshold
        self._last_standardized: np.ndarray | None = None
        self._current_sti: float = 0.0

    def reset(self) -> None:
        self._last_standardized = None
        self._current_sti = 0.0

    def update(self, subcarrier_amplitudes: np.ndarray) -> float:
        """輸入單一時間點的子載波振幅，回傳該時間點的 STI。

        Parameters
        ----------
        subcarrier_amplitudes : np.ndarray, shape (n_subcarriers,)

        Returns
        -------
        sti : float
            已正規化的 STI 值。
        """
        curr_std = _standardize_vector(subcarrier_amplitudes)

        if self._last_standardized is None:
            self._last_standardized = curr_std
            self._current_sti = 0.0
            return 0.0

        n = len(subcarrier_amplitudes)
        norm_factor = np.sqrt(n) if n > 0 else 1.0
        diff = curr_std - self._last_standardized
        self._current_sti = float(np.linalg.norm(diff) / norm_factor)
        self._last_standardized = curr_std
        return self._current_sti

    @property
    def current_sti(self) -> float:
        return self._current_sti

    @property
    def is_above_threshold(self) -> bool:
        return self._current_sti > self.threshold
