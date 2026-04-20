"""
CSI 資料讀取器。

負責：
- 從 ESP32CSISource 即時讀取 CSI 資料
- 從 CSV 檔案讀取歷史 CSI 資料
- 產生模擬跌倒 / 正常行為資料供測試
- 擷取子載波振幅，輸出時間序列資料
"""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

NUM_SUBCARRIERS = 52


@dataclass
class CSISample:
    """單筆 CSI 取樣。"""

    timestamp: float
    amplitudes: np.ndarray  # shape: (NUM_SUBCARRIERS,)
    rssi_dbm: float = -70.0


class RealtimeCSIReader:
    """從 ESP32CSISource 即時讀取 CSI 振幅資料。"""

    def __init__(self, csi_source):
        """
        Parameters
        ----------
        csi_source : ESP32CSISource
            已啟動的 CSI 資料來源。
        """
        self._source = csi_source

    def read_window(self, window_size: int = 100) -> list[CSISample]:
        """取得最近 window_size 筆 CSI 取樣。"""
        frames = self._source.get_latest_frames(n=window_size)
        samples = []
        for f in frames:
            samples.append(
                CSISample(
                    timestamp=f.timestamp,
                    amplitudes=f.amplitudes.copy(),
                    rssi_dbm=f.rssi_dbm,
                )
            )
        return samples

    def read_latest(self) -> Optional[CSISample]:
        """取得最新一筆 CSI 取樣。"""
        f = self._source.get_latest_frame()
        if f is None:
            return None
        return CSISample(
            timestamp=f.timestamp,
            amplitudes=f.amplitudes.copy(),
            rssi_dbm=f.rssi_dbm,
        )


class CSVCSIReader:
    """從 CSV 檔讀取 CSI 振幅資料（離線分析用）。

    CSV 格式須包含欄位：
    - timestamp (float)
    - amp_0, amp_1, ..., amp_51 (52 個子載波振幅)
    - rssi_dbm (選填)
    """

    def __init__(self, filepath: str):
        self._filepath = filepath
        self._samples: list[CSISample] = []
        self._load()

    def _load(self) -> None:
        with open(self._filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = float(row["timestamp"])
                amps = np.array(
                    [float(row.get(f"amp_{i}", 0.0)) for i in range(NUM_SUBCARRIERS)],
                    dtype=np.float64,
                )
                rssi = float(row.get("rssi_dbm", -70.0))
                self._samples.append(CSISample(timestamp=ts, amplitudes=amps, rssi_dbm=rssi))
        logger.info("Loaded %d CSI samples from %s", len(self._samples), self._filepath)

    @property
    def samples(self) -> list[CSISample]:
        return list(self._samples)

    def read_window(self, start_idx: int, window_size: int) -> list[CSISample]:
        """取得指定區間的 CSI 取樣。"""
        end_idx = min(start_idx + window_size, len(self._samples))
        return self._samples[start_idx:end_idx]


class SimulatedCSIReader:
    """產生模擬 CSI 資料供測試使用。

    可模擬三種狀態：normal（靜止/走動）、motion（大動作）、fall（跌倒）。
    跌倒模式使用固定的 fall_shift 特徵向量，可用於同步建立模板。
    """

    # 固定的跌倒特徵偏移（用於模板同步）
    _FALL_SHIFT_SEED = 99

    def __init__(self, sample_rate: float = 20.0, seed: int = 42):
        self._sample_rate = sample_rate
        self._rng = np.random.default_rng(seed)
        self._base_amplitude = self._rng.uniform(0.3, 0.8, size=NUM_SUBCARRIERS)
        # 跌倒特徵偏移使用獨立 seed 確保可重複
        fall_rng = np.random.default_rng(self._FALL_SHIFT_SEED)
        self._fall_shift = fall_rng.uniform(-0.3, 0.3, size=NUM_SUBCARRIERS)

    @property
    def base_amplitude(self) -> np.ndarray:
        return self._base_amplitude.copy()

    @property
    def fall_shift(self) -> np.ndarray:
        return self._fall_shift.copy()

    def generate_normal(self, duration: float = 10.0) -> list[CSISample]:
        """產生靜止/正常狀態的 CSI 資料。"""
        n_samples = int(duration * self._sample_rate)
        t0 = time.time()
        samples = []
        for i in range(n_samples):
            noise = self._rng.normal(0, 0.01, size=NUM_SUBCARRIERS)
            amps = np.clip(self._base_amplitude + noise, 0, 2.0)
            samples.append(
                CSISample(timestamp=t0 + i / self._sample_rate, amplitudes=amps)
            )
        return samples

    def generate_fall_event(self, pre_seconds: float = 3.0, fall_seconds: float = 1.0,
                            post_seconds: float = 6.0) -> list[CSISample]:
        """產生包含跌倒事件的 CSI 資料序列。

        結構：靜止 → 跌倒瞬間（劇烈變化）→ 倒地靜止（模式改變）。
        """
        t0 = time.time()
        samples = []
        idx = 0

        # 跌倒前：靜止
        n_pre = int(pre_seconds * self._sample_rate)
        for i in range(n_pre):
            noise = self._rng.normal(0, 0.01, size=NUM_SUBCARRIERS)
            amps = np.clip(self._base_amplitude + noise, 0, 2.0)
            samples.append(CSISample(timestamp=t0 + idx / self._sample_rate, amplitudes=amps))
            idx += 1

        # 跌倒瞬間：振幅劇烈變化
        n_fall = int(fall_seconds * self._sample_rate)
        for i in range(n_fall):
            progress = i / max(n_fall - 1, 1)
            shift = self._fall_shift * progress
            noise = self._rng.normal(0, 0.04, size=NUM_SUBCARRIERS)
            amps = np.clip(self._base_amplitude + shift + noise, 0, 2.0)
            samples.append(CSISample(timestamp=t0 + idx / self._sample_rate, amplitudes=amps))
            idx += 1

        # 跌倒後：新的穩定模式（人倒在地上）
        fallen_amplitude = self._base_amplitude + self._fall_shift
        n_post = int(post_seconds * self._sample_rate)
        for i in range(n_post):
            noise = self._rng.normal(0, 0.01, size=NUM_SUBCARRIERS)
            amps = np.clip(fallen_amplitude + noise, 0, 2.0)
            samples.append(CSISample(timestamp=t0 + idx / self._sample_rate, amplitudes=amps))
            idx += 1

        return samples

    def generate_walking(self, duration: float = 10.0) -> list[CSISample]:
        """產生走動狀態的 CSI 資料（有週期性變動但無跌倒模式）。"""
        n_samples = int(duration * self._sample_rate)
        t0 = time.time()
        samples = []
        for i in range(n_samples):
            phase = 2.0 * np.pi * i / (self._sample_rate * 2.0)  # ~0.5 Hz 週期
            walk_variation = 0.08 * np.sin(
                phase + np.linspace(0, np.pi, NUM_SUBCARRIERS)
            )
            noise = self._rng.normal(0, 0.02, size=NUM_SUBCARRIERS)
            amps = np.clip(self._base_amplitude + walk_variation + noise, 0, 2.0)
            samples.append(
                CSISample(timestamp=t0 + i / self._sample_rate, amplitudes=amps)
            )
        return samples

    def build_fall_template(self, window_size: int = 40) -> np.ndarray:
        """根據本模擬器的跌倒特徵建立模板矩陣。

        模板反映跌倒事件的 CSI 矩陣型態：
        前 1/3 穩定 → 中間 1/6 突變 → 後 1/2 新穩態。
        """
        template = np.zeros((NUM_SUBCARRIERS, window_size), dtype=np.float64)
        phase_1_end = window_size // 3
        phase_2_end = phase_1_end + window_size // 6

        for t in range(phase_1_end):
            template[:, t] = self._base_amplitude

        for t in range(phase_1_end, phase_2_end):
            progress = (t - phase_1_end) / max(phase_2_end - phase_1_end - 1, 1)
            template[:, t] = self._base_amplitude + self._fall_shift * progress

        fallen = self._base_amplitude + self._fall_shift
        for t in range(phase_2_end, window_size):
            template[:, t] = fallen

        return np.clip(template, 0, 2.0)
