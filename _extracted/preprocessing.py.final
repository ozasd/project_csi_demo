"""
CSI 資料前處理模組。

負責：
- 去除雜訊（移動平均平滑）
- 異常值去除
- 正規化
- 切出固定時間窗口
- 將 CSI 取樣整理成矩陣格式（子載波 × 時間）
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter1d

from .csi_reader import CSISample, NUM_SUBCARRIERS


def samples_to_matrix(samples: list[CSISample]) -> tuple[np.ndarray, np.ndarray]:
    """將 CSI 取樣清單轉換為振幅矩陣。

    Returns
    -------
    amplitude_matrix : np.ndarray, shape (NUM_SUBCARRIERS, T)
        橫軸為時間，縱軸為子載波編號。
    timestamps : np.ndarray, shape (T,)
        對應的時間戳。
    """
    if not samples:
        return np.empty((NUM_SUBCARRIERS, 0)), np.empty(0)

    T = len(samples)
    matrix = np.zeros((NUM_SUBCARRIERS, T), dtype=np.float64)
    timestamps = np.zeros(T, dtype=np.float64)

    for i, s in enumerate(samples):
        matrix[:, i] = s.amplitudes[:NUM_SUBCARRIERS]
        timestamps[i] = s.timestamp

    return matrix, timestamps


def remove_outliers(matrix: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    """以 Z-score 去除異常值，超出 ±sigma 的值替換為該子載波的中位數。"""
    result = matrix.copy()
    for i in range(result.shape[0]):
        row = result[i]
        median = np.median(row)
        std = np.std(row)
        if std < 1e-12:
            continue
        z = np.abs((row - median) / std)
        result[i, z > sigma] = median
    return result


def smooth(matrix: np.ndarray, window: int = 3) -> np.ndarray:
    """沿時間軸做移動平均平滑。"""
    if window < 2:
        return matrix.copy()
    return uniform_filter1d(matrix, size=window, axis=1, mode="nearest")


def normalize(matrix: np.ndarray) -> np.ndarray:
    """對每個子載波做 min-max 正規化至 [0, 1]。"""
    result = matrix.copy()
    for i in range(result.shape[0]):
        row = result[i]
        vmin, vmax = row.min(), row.max()
        span = vmax - vmin
        if span < 1e-12:
            result[i] = 0.0
        else:
            result[i] = (row - vmin) / span
    return result


def preprocess(
    samples: list[CSISample],
    smooth_window: int = 3,
    outlier_sigma: float = 3.0,
    do_normalize: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """完整前處理流程。

    Parameters
    ----------
    samples : list[CSISample]
        原始 CSI 取樣。
    smooth_window : int
        移動平均窗口大小。
    outlier_sigma : float
        異常值判定 Z-score 門檻。
    do_normalize : bool
        是否做 min-max 正規化。

    Returns
    -------
    processed_matrix : np.ndarray, shape (NUM_SUBCARRIERS, T)
    timestamps : np.ndarray, shape (T,)
    """
    matrix, timestamps = samples_to_matrix(samples)
    if matrix.size == 0:
        return matrix, timestamps

    matrix = remove_outliers(matrix, sigma=outlier_sigma)
    matrix = smooth(matrix, window=smooth_window)
    if do_normalize:
        matrix = normalize(matrix)

    return matrix, timestamps


def extract_time_window(
    samples: list[CSISample],
    window_seconds: float = 2.0,
) -> list[CSISample]:
    """從取樣清單的尾端擷取指定秒數的時間窗口。"""
    if not samples:
        return []
    t_end = samples[-1].timestamp
    t_start = t_end - window_seconds
    return [s for s in samples if s.timestamp >= t_start]
