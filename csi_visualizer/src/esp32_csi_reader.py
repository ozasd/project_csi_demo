"""
ESP32 CSI 序列埠讀取與場景基線模型。

這個模組負責兩件事：
1. 從 ESP32 `CSI_DATA,...` 序列輸出解析出 52 個子載波的振幅與相位。
2. 在啟動初期建立「靜態空間基線」，之後持續計算目前場景相對於基線的差分，
   供雲霧圖與輪廓代理視覺化使用。

注意：
- 這裡的「輪廓」是 CSI 差分後的代理訊號，不是真正的 2D/3D 幾何輪廓。
- 若啟動時場景中已經有人或物體，該狀態會一起被視為背景基線。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Protocol

import numpy as np

from src import config as cfg
from src.serial_utils import open_serial_without_reset, release_serial_control_lines

logger = logging.getLogger(__name__)


def _copy_optional_array(values: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """回傳 numpy 陣列的安全副本。"""
    if values is None:
        return None
    return values.copy()


@dataclass
class CSIFrame:
    """單筆 CSI 影格。"""

    timestamp: float
    subcarrier_count: int = 52
    amplitudes: np.ndarray = field(default_factory=lambda: np.zeros(52, dtype=np.float64))
    phases: np.ndarray = field(default_factory=lambda: np.zeros(52, dtype=np.float64))
    rssi_dbm: float = -70.0
    is_simulated: bool = False


@dataclass
class SceneSnapshot:
    """
    場景快照。

    baseline_profile:
        初始化期間蒐集到的靜態基線振幅平均值。
    current_profile:
        最近一段時間平滑後的目前場景振幅。
    delta_profile:
        目前場景與基線的絕對差分。
    silhouette_profile:
        由差分估算出的「輪廓代理強度」，範圍約 0~1。
    """

    timestamp: float = 0.0
    baseline_profile: Optional[np.ndarray] = None
    current_profile: Optional[np.ndarray] = None
    delta_profile: Optional[np.ndarray] = None
    silhouette_profile: Optional[np.ndarray] = None
    motion_energy: float = 0.0
    foreground_ratio: float = 0.0
    peak_delta: float = 0.0
    calibration_progress: float = 0.0
    is_calibrated: bool = False
    baseline_frames: int = 0
    collected_baseline_frames: int = 0


class CSISource(Protocol):
    """CSI 資料來源協定。"""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def get_latest_frames(self, n: int = 100) -> list[CSIFrame]: ...
    def get_latest_frame(self) -> Optional[CSIFrame]: ...
    def get_scene_snapshot(self) -> SceneSnapshot: ...


class ESP32CSISource:
    """
    從 ESP32 序列埠讀取真實 CSI，並建立靜態空間基線模型。

    流程：
    1. 啟動後先蒐集 `baseline_frames` 筆 CSI 作為背景基線。
    2. 基線完成後，持續以滑動平均取得目前場景。
    3. 以 `|current - baseline|` 估算前景差分與輪廓代理。
    """

    _TARGET_SUBCARRIERS = 52
    _LLTF_COMPLEX_PAIRS = 64
    _LLTF_VALID_NEGATIVE = list(range(6, 32))
    _LLTF_VALID_POSITIVE = list(range(33, 59))

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 921600,
        baseline_frames: int = 80,
        smoothing_window: int = 3,
        scene_window: int = 6,
        contour_sigma: float = 1.6,
        contour_floor: float = 0.018,
    ):
        self.port = port
        self.baudrate = baudrate
        self._baseline_frames = max(10, baseline_frames)
        self._smoothing_window = max(1, smoothing_window)
        self._scene_window = max(1, scene_window)
        self._contour_sigma = max(1.0, contour_sigma)
        self._contour_floor = max(0.005, contour_floor)

        self._frames: deque[CSIFrame] = deque(maxlen=2000)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._serial = None

        self._amp_buffer: deque[np.ndarray] = deque(maxlen=self._smoothing_window)
        self._scene_buffer: deque[np.ndarray] = deque(maxlen=self._scene_window)
        self._baseline_buffer: list[np.ndarray] = []
        self._baseline_mean: Optional[np.ndarray] = None
        self._baseline_std: Optional[np.ndarray] = None
        self._scene_snapshot = SceneSnapshot(baseline_frames=self._baseline_frames)
        self._low_amplitude_threshold = cfg.CSI_LOW_AMPLITUDE_THRESHOLD
        self._low_amplitude_streak = cfg.CSI_LOW_AMPLITUDE_STREAK
        self._low_amplitude_counts = np.zeros(self._TARGET_SUBCARRIERS, dtype=np.int32)
        self._active_drop_events = 0
        self._center_null_frames = 0

        self._total_lines = 0
        self._parsed_frames = 0
        self._parse_errors = 0

    def start(self) -> None:
        """開啟序列埠並啟動背景讀取執行緒。"""
        try:
            import serial
        except ImportError as exc:
            raise ImportError(
                "需要安裝 pyserial：pip install pyserial\n"
                "或在 conda 環境中：conda activate wifi-csi && pip install pyserial"
            ) from exc

        logger.info("Opening serial %s @ %s", self.port, self.baudrate)
        self._serial = open_serial_without_reset(
            port=self.port,
            baudrate=self.baudrate,
            timeout=1,
        )
        self._serial.reset_input_buffer()

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止背景讀取，並關閉序列埠。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._serial and self._serial.is_open:
            release_serial_control_lines(self._serial)
            self._serial.close()

    def reset_scene_baseline(self) -> None:
        """重新開始靜態空間初始化。"""
        with self._lock:
            self._amp_buffer.clear()
            self._scene_buffer.clear()
            self._baseline_buffer.clear()
            self._baseline_mean = None
            self._baseline_std = None
            self._scene_snapshot = SceneSnapshot(baseline_frames=self._baseline_frames)

    def get_latest_frames(self, n: int = 100) -> list[CSIFrame]:
        """取得最近 N 筆 CSI 影格。"""
        with self._lock:
            return list(self._frames)[-n:]

    def get_latest_frame(self) -> Optional[CSIFrame]:
        """取得最新一筆 CSI 影格。"""
        with self._lock:
            if self._frames:
                return self._frames[-1]
        return None

    def get_scene_snapshot(self) -> SceneSnapshot:
        """取得最新場景快照。"""
        with self._lock:
            snap = self._scene_snapshot
            return SceneSnapshot(
                timestamp=snap.timestamp,
                baseline_profile=_copy_optional_array(snap.baseline_profile),
                current_profile=_copy_optional_array(snap.current_profile),
                delta_profile=_copy_optional_array(snap.delta_profile),
                silhouette_profile=_copy_optional_array(snap.silhouette_profile),
                motion_energy=snap.motion_energy,
                foreground_ratio=snap.foreground_ratio,
                peak_delta=snap.peak_delta,
                calibration_progress=snap.calibration_progress,
                is_calibrated=snap.is_calibrated,
                baseline_frames=snap.baseline_frames,
                collected_baseline_frames=snap.collected_baseline_frames,
            )

    @property
    def stats(self) -> dict:
        """回傳序列埠解析與場景模型狀態。"""
        snapshot = self.get_scene_snapshot()
        return {
            "total_lines": self._total_lines,
            "parsed_frames": self._parsed_frames,
            "parse_errors": self._parse_errors,
            "scene_ready": snapshot.is_calibrated,
            "scene_progress": round(snapshot.calibration_progress, 3),
            "motion_energy": round(snapshot.motion_energy, 4),
            "foreground_ratio": round(snapshot.foreground_ratio, 4),
            "center_null_frames": self._center_null_frames,
            "active_drop_events": self._active_drop_events,
        }

    @property
    def scene_calibration_progress(self) -> float:
        """目前靜態空間初始化進度，0.0 ~ 1.0。"""
        return self.get_scene_snapshot().calibration_progress

    @property
    def is_scene_calibrated(self) -> bool:
        """是否已完成靜態空間初始化。"""
        return self.get_scene_snapshot().is_calibrated

    def _read_loop(self) -> None:
        """持續從序列埠讀取資料，只處理 `CSI_DATA` 行。"""
        while self._running:
            try:
                if not self._serial or not self._serial.is_open:
                    time.sleep(0.1)
                    continue

                raw = self._serial.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                self._total_lines += 1
                if not line.startswith("CSI_DATA"):
                    continue

                frame = self._parse_csi_line(line)
                if frame is None:
                    continue

                self._parsed_frames += 1
                with self._lock:
                    self._frames.append(frame)

            except Exception as exc:
                self._parse_errors += 1
                if not self._running:
                    break
                logger.debug("CSI read error: %s", exc)
                time.sleep(0.02)

    def _parse_csi_line(self, line: str) -> Optional[CSIFrame]:
        """解析單行 `CSI_DATA,...` 文字。"""
        try:
            data_start = line.find('"[')
            data_end = line.rfind(']"')
            if data_start < 0 or data_end < 0:
                return None

            metadata = line[:data_start].rstrip(",")
            data_block = line[data_start : data_end + 2]

            parts = metadata.split(",")
            if len(parts) < 24:
                return None

            rssi_dbm = float(parts[3])
            payload = data_block.strip('"').strip("[]")
            raw_values = [int(item.strip()) for item in payload.split(",") if item.strip()]

            amplitudes, phases = self._extract_amplitudes_phases(raw_values)
            if amplitudes is None or phases is None:
                return None

            amplitudes = self._smooth_amplitudes(amplitudes)
            self._monitor_active_subcarriers(amplitudes)
            phases = np.unwrap(phases)
            self._update_scene_model(amplitudes)

            return CSIFrame(
                timestamp=time.time(),
                subcarrier_count=len(amplitudes),
                amplitudes=amplitudes,
                phases=phases,
                rssi_dbm=rssi_dbm,
                is_simulated=False,
            )
        except (ValueError, IndexError) as exc:
            self._parse_errors += 1
            logger.debug("CSI parse error: %s", exc)
            return None

    def _extract_amplitudes_phases(
        self, raw_values: list[int]
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        從 `[imag0, real0, imag1, real1, ...]` 取出振幅與相位。

        ESP32 的 CSI 資料前後通常帶有 header / padding，因此會先裁掉
        `_HEADER_PAIRS` 與 `_TAIL_PAIRS`，再內插到固定 52 個子載波。
        """
        if len(raw_values) < 4:
            return None, None

        # 對於目前這個專案的 ESP32 + csi_recv_router，韌體端只開 LLTF。
        # 實際輸出的 64 complex pairs 內，52 個有效子載波落在：
        # - 6..31
        # - 33..58
        # 也就是：
        # - 0..5   : guard / null
        # - 32     : 中心 DC / null
        # - 59..63 : guard / null
        #
        # 這個分佈可從 Espressif get-started README 的 len=128 範例資料直接驗證。
        # 若把整段 64 pair 直接硬切、重採樣，會在圖中央製造一整段假的 0 值凹洞。
        pair_count = len(raw_values) // 2
        imag_all = np.array(raw_values[0::2], dtype=np.float64)
        real_all = np.array(raw_values[1::2], dtype=np.float64)
        if imag_all.size == 0 or real_all.size == 0:
            return None, None

        if pair_count >= self._LLTF_COMPLEX_PAIRS:
            if pair_count > self._LLTF_COMPLEX_PAIRS:
                # 若收到多個 LTF 區段，優先只取最前面的 LLTF 64 complex pairs，
                # 避免把 LLTF / HT-LTF 串在一起後又硬壓成 52 點。
                imag_all = imag_all[: self._LLTF_COMPLEX_PAIRS]
                real_all = real_all[: self._LLTF_COMPLEX_PAIRS]

            if np.hypot(imag_all[32], real_all[32]) <= 1e-6:
                self._center_null_frames += 1

            negative_imag = imag_all[self._LLTF_VALID_NEGATIVE]
            negative_real = real_all[self._LLTF_VALID_NEGATIVE]
            positive_imag = imag_all[self._LLTF_VALID_POSITIVE]
            positive_real = real_all[self._LLTF_VALID_POSITIVE]

            # 重新排成自然頻率順序：負頻到正頻。
            imag = np.concatenate([negative_imag, positive_imag])
            real = np.concatenate([negative_real, positive_real])
        else:
            imag = imag_all
            real = real_all

        amplitudes = np.sqrt(imag**2 + real**2)
        phases = np.arctan2(imag, real)

        if len(amplitudes) != self._TARGET_SUBCARRIERS:
            target_axis = np.linspace(0, len(amplitudes) - 1, self._TARGET_SUBCARRIERS)
            source_axis = np.arange(len(amplitudes))
            amplitudes = np.interp(target_axis, source_axis, amplitudes)
            phases = np.interp(target_axis, source_axis, phases)

        # 固定比例的對數壓縮，保留跨影格比較的一致性。
        amplitudes = np.log1p(amplitudes) / 3.8
        amplitudes = np.clip(amplitudes, 0.0, 2.0)
        return amplitudes, phases

    def _monitor_active_subcarriers(self, amplitudes: np.ndarray) -> None:
        """
        監控有效子載波是否長時間掉到過低振幅。

        這個監控不會把 DC / null carrier 算進去，因為它們已在解析階段排除。
        """
        low_mask = amplitudes <= self._low_amplitude_threshold
        self._low_amplitude_counts[low_mask] += 1
        self._low_amplitude_counts[~low_mask] = 0

        triggered_indices = np.where(self._low_amplitude_counts == self._low_amplitude_streak)[0]
        if triggered_indices.size == 0:
            return

        self._active_drop_events += int(triggered_indices.size)
        logger.warning(
            "偵測到有效子載波長時間低振幅: subcarriers=%s threshold=%.3f streak=%s",
            triggered_indices.tolist(),
            self._low_amplitude_threshold,
            self._low_amplitude_streak,
        )

    def _smooth_amplitudes(self, amplitudes: np.ndarray) -> np.ndarray:
        """以短窗口平均減少單幀抖動。"""
        self._amp_buffer.append(amplitudes.copy())
        if len(self._amp_buffer) == 1:
            return amplitudes
        return np.mean(np.stack(self._amp_buffer, axis=0), axis=0)

    def _update_scene_model(self, amplitudes: np.ndarray) -> None:
        """
        更新靜態基線、目前場景、差分與輪廓代理。

        輪廓代理邏輯：
        - 先以 `delta = |current - baseline|` 取得差分。
        - 再用基線標準差估計噪聲底，超過噪聲後才視為前景。
        - 最後做一次簡單平滑，讓輪廓線比較連續。
        """
        self._scene_buffer.append(amplitudes.copy())
        current_profile = np.mean(np.stack(self._scene_buffer, axis=0), axis=0)

        if self._baseline_mean is None:
            self._baseline_buffer.append(amplitudes.copy())
            progress = min(1.0, len(self._baseline_buffer) / self._baseline_frames)

            if len(self._baseline_buffer) >= self._baseline_frames:
                stacked = np.stack(self._baseline_buffer, axis=0)
                self._baseline_mean = np.mean(stacked, axis=0)
                self._baseline_std = np.std(stacked, axis=0)
                self._baseline_std = np.clip(self._baseline_std, self._contour_floor, None)
                logger.info("Scene baseline locked with %s CSI frames.", len(self._baseline_buffer))

            snapshot = SceneSnapshot(
                timestamp=time.time(),
                baseline_profile=_copy_optional_array(self._baseline_mean),
                current_profile=current_profile.copy(),
                delta_profile=np.zeros_like(current_profile),
                silhouette_profile=np.zeros_like(current_profile),
                motion_energy=0.0,
                foreground_ratio=0.0,
                peak_delta=0.0,
                calibration_progress=progress if self._baseline_mean is None else 1.0,
                is_calibrated=self._baseline_mean is not None,
                baseline_frames=self._baseline_frames,
                collected_baseline_frames=len(self._baseline_buffer),
            )
            with self._lock:
                self._scene_snapshot = snapshot
            return

        delta_profile = np.abs(current_profile - self._baseline_mean)
        noise_floor = np.maximum(self._baseline_std * self._contour_sigma, self._contour_floor)
        normalized = np.clip((delta_profile - noise_floor * 0.6) / (noise_floor * 2.2), 0.0, 1.0)

        # 讓輪廓代理較連續，不會因單一子載波尖峰過於鋸齒。
        kernel = np.array([0.15, 0.35, 1.0, 0.35, 0.15], dtype=np.float64)
        silhouette_profile = np.convolve(normalized, kernel, mode="same") / kernel.sum()

        snapshot = SceneSnapshot(
            timestamp=time.time(),
            baseline_profile=self._baseline_mean.copy(),
            current_profile=current_profile.copy(),
            delta_profile=delta_profile.copy(),
            silhouette_profile=silhouette_profile.copy(),
            motion_energy=float(np.mean(delta_profile)),
            foreground_ratio=float(np.mean(silhouette_profile >= 0.35)),
            peak_delta=float(np.max(delta_profile)),
            calibration_progress=1.0,
            is_calibrated=True,
            baseline_frames=self._baseline_frames,
            collected_baseline_frames=len(self._baseline_buffer),
        )
        with self._lock:
            self._scene_snapshot = snapshot
