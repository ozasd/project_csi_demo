"""
Wi-Fi RSSI 動態偵測系統 — 動態偵測引擎 v2
=============================================
針對整數 RSSI（1 dBm 解析度、~2 Hz 取樣率）優化的多維度偵測演算法。

核心改進：
- 變化頻率 (transition_rate)：整數 RSSI 的最佳指標
- 差分梯度 (gradient)：捕捉快速跳動
- 短/長窗口比較：捕捉「突然波動」
- 組合評分 (composite_score)：加權多指標，比單一閾值更穩定
- 基線自適應僅更新 mean，不向下壓縮 std/range
"""

import time
import numpy as np
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.wifi_scanner import RSSISample


class MotionState(Enum):
    """偵測狀態"""
    CALIBRATING = "校準中"
    IDLE = "靜止"
    MOTION = "偵測到移動"


@dataclass
class MotionEvent:
    """動態偵測事件"""
    timestamp: float
    state: MotionState
    confidence: float           # 0.0 ~ 1.0
    rssi_mean: float
    rssi_std: float
    rssi_range: float
    motion_score: float
    transition_rate: float = 0.0
    gradient: float = 0.0
    composite_score: float = 0.0


class MotionDetector:
    """基於多維度特徵的 RSSI 動態偵測器

    演算法概述（v2 — 針對整數 RSSI 優化）：
    1. 校準期收集靜態基線（mean, std, range, transition_rate, gradient）
    2. 偵測期計算四個維度的特徵：
       - std_ratio：標準差與基線的比值
       - range_ratio：極差與基線的比值
       - transition_rate：窗口內 RSSI 值變化的次數比率
       - gradient：相鄰取樣差值的絕對值平均
    3. 加權計算 composite_score，超過閾值觸發偵測
    4. 基線僅自適應 mean（不向下壓縮 std/range 避免閾值失效）
    """

    # 組合評分的特徵權重
    W_STD = 0.25
    W_RANGE = 0.20
    W_TRANSITION = 0.30
    W_GRADIENT = 0.25

    def __init__(
        self,
        window_size: int = 15,
        short_window_size: int = 8,
        long_window_size: int = 50,
        baseline_duration: float = 8.0,
        threshold_std: float = 0.5,
        threshold_range: float = 2.0,
        threshold_transition: float = 0.25,
        threshold_gradient: float = 0.3,
        threshold_composite: float = 1.8,
        hold_time: float = 1.5,
        debounce_count: int = 2,
        adapt_rate: float = 0.002,
    ):
        self.window_size = window_size
        self.short_window_size = short_window_size
        self.long_window_size = long_window_size
        self.baseline_duration = baseline_duration
        self.threshold_std = threshold_std
        self.threshold_range = threshold_range
        self.threshold_transition = threshold_transition
        self.threshold_gradient = threshold_gradient
        self.threshold_composite = threshold_composite
        self.hold_time = hold_time
        self.debounce_count = debounce_count
        self.adapt_rate = adapt_rate

        # 內部狀態 — 多尺度窗口
        self._window: deque[float] = deque(maxlen=window_size)
        self._short_window: deque[float] = deque(maxlen=short_window_size)
        self._long_window: deque[float] = deque(maxlen=long_window_size)
        self._state = MotionState.CALIBRATING
        self._calibration_start: Optional[float] = None
        self._calibration_values: list[float] = []

        # 基線統計
        self._baseline_mean: float = 0.0
        self._baseline_std: float = 0.5
        self._baseline_range: float = 1.0
        self._baseline_transition: float = 0.05
        self._baseline_gradient: float = 0.1

        # 偵測狀態
        self._trigger_count: int = 0
        self._last_motion_time: float = 0.0
        self._detection_count: int = 0

        # 歷史記錄
        self.events: deque[MotionEvent] = deque(maxlen=500)
        self.motion_scores: deque[float] = deque(maxlen=2000)
        self.rssi_stds: deque[float] = deque(maxlen=2000)
        self.transition_rates: deque[float] = deque(maxlen=2000)
        self.gradients: deque[float] = deque(maxlen=2000)
        self.composite_scores: deque[float] = deque(maxlen=2000)
        self.timestamps: deque[float] = deque(maxlen=2000)

    @property
    def state(self) -> MotionState:
        return self._state

    @property
    def detection_count(self) -> int:
        return self._detection_count

    @property
    def calibration_progress(self) -> float:
        """校準進度 0.0 ~ 1.0"""
        if self._state != MotionState.CALIBRATING:
            return 1.0
        if self._calibration_start is None:
            return 0.0
        elapsed = time.time() - self._calibration_start
        return min(1.0, elapsed / self.baseline_duration)

    # ──────────────────────────────────────────────
    #  靜態工具：計算特徵
    # ──────────────────────────────────────────────

    @staticmethod
    def _calc_transition_rate(values) -> float:
        """計算窗口內 RSSI 值變化的次數比率。
        整數 RSSI 靜止時連續相同，移動時頻繁跳動。
        """
        if len(values) < 2:
            return 0.0
        transitions = sum(
            1 for i in range(1, len(values)) if values[i] != values[i - 1]
        )
        return transitions / (len(values) - 1)

    @staticmethod
    def _calc_gradient(values) -> float:
        """計算相鄰取樣差值的絕對值平均（平均梯度）。"""
        if len(values) < 2:
            return 0.0
        diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
        return sum(diffs) / len(diffs)

    # ──────────────────────────────────────────────
    #  主要介面
    # ──────────────────────────────────────────────

    def feed(self, sample: RSSISample) -> MotionEvent:
        """餵入新的 RSSI 取樣，回傳當前偵測結果"""
        rssi = sample.rssi_dbm
        now = sample.timestamp

        # ── 校準階段 ──
        if self._state == MotionState.CALIBRATING:
            return self._handle_calibration(rssi, now)

        # ── 偵測階段 ──
        self._window.append(rssi)
        self._short_window.append(rssi)
        self._long_window.append(rssi)

        if len(self._window) < 5:
            return self._make_event(now, MotionState.IDLE, 0.0, 0.0)

        # ── 計算多維度特徵 ──
        arr = np.array(self._window)
        current_mean = float(np.mean(arr))
        current_std = float(np.std(arr))
        current_range = float(np.max(arr) - np.min(arr))

        win_list = list(self._window)
        current_transition = self._calc_transition_rate(win_list)
        current_gradient = self._calc_gradient(win_list)

        # 短窗口 std（快速反應）
        if len(self._short_window) >= 3:
            short_arr = np.array(self._short_window)
            short_std = float(np.std(short_arr))
        else:
            short_std = current_std

        # ── 計算各維度的比率/分數 ──
        std_ratio = current_std / max(self._baseline_std, 0.1)
        range_ratio = current_range / max(self._baseline_range, 0.5)
        short_std_ratio = short_std / max(self._baseline_std, 0.1)
        trans_score = current_transition / max(self._baseline_transition, 0.02)
        grad_score = current_gradient / max(self._baseline_gradient, 0.05)

        # 取 std_ratio 和 short_std_ratio 的較大值（短窗口更靈敏）
        effective_std_ratio = max(std_ratio, short_std_ratio * 0.8)

        # ── 組合評分 ──
        composite_score = (
            self.W_STD * effective_std_ratio
            + self.W_RANGE * range_ratio
            + self.W_TRANSITION * trans_score
            + self.W_GRADIENT * grad_score
        )

        # 向後相容的舊 motion_score
        motion_score = max(std_ratio, range_ratio)

        # ── 判定：組合評分超閾值，或任兩個個別指標同時超閾值 ──
        composite_triggered = composite_score > self.threshold_composite
        individual_flags = [
            current_std > self.threshold_std,
            current_range > self.threshold_range,
            current_transition > self.threshold_transition,
            current_gradient > self.threshold_gradient,
        ]
        multi_triggered = sum(individual_flags) >= 2
        is_triggered = composite_triggered or multi_triggered

        if is_triggered:
            self._trigger_count += 1
        else:
            self._trigger_count = max(0, self._trigger_count - 1)

        # ── 狀態轉換 ──
        prev_state = self._state

        if self._trigger_count >= self.debounce_count:
            self._state = MotionState.MOTION
            self._last_motion_time = now
            if prev_state != MotionState.MOTION:
                self._detection_count += 1
        elif self._state == MotionState.MOTION:
            if now - self._last_motion_time > self.hold_time:
                self._state = MotionState.IDLE
                self._trigger_count = 0
        else:
            self._state = MotionState.IDLE

        # ── 自適應更新基線（僅在靜止時，僅更新 mean；std/range 只向上調不向下壓）──
        if self._state == MotionState.IDLE:
            r = self.adapt_rate
            self._baseline_mean = self._baseline_mean * (1 - r) + current_mean * r
            if current_std > self._baseline_std:
                self._baseline_std = self._baseline_std * (1 - r) + current_std * r
            if current_range > self._baseline_range:
                self._baseline_range = self._baseline_range * (1 - r) + current_range * r

        # ── 計算信心度 ──
        confidence = min(1.0, max(0.0, (composite_score - self.threshold_composite) / 3.0))

        # ── 記錄 ──
        self.motion_scores.append(motion_score)
        self.rssi_stds.append(current_std)
        self.transition_rates.append(current_transition)
        self.gradients.append(current_gradient)
        self.composite_scores.append(composite_score)
        self.timestamps.append(now)

        event = self._make_event(
            now, self._state, confidence, motion_score,
            rssi_mean=current_mean, rssi_std=current_std,
            rssi_range=current_range,
            transition_rate=current_transition,
            gradient=current_gradient,
            composite_score=composite_score,
        )
        self.events.append(event)
        return event

    # ──────────────────────────────────────────────
    #  內部方法
    # ──────────────────────────────────────────────

    def _handle_calibration(self, rssi: float, now: float) -> MotionEvent:
        """處理校準階段"""
        if self._calibration_start is None:
            self._calibration_start = now

        self._calibration_values.append(rssi)
        self._window.append(rssi)
        self._short_window.append(rssi)
        self._long_window.append(rssi)

        elapsed = now - self._calibration_start
        if elapsed >= self.baseline_duration and len(self._calibration_values) >= 10:
            # 校準完成，建立多維度基線
            arr = np.array(self._calibration_values)
            self._baseline_mean = float(np.mean(arr))
            self._baseline_std = float(np.std(arr))
            self._baseline_range = float(np.max(arr) - np.min(arr))

            vals = list(self._calibration_values)
            self._baseline_transition = self._calc_transition_rate(vals)
            self._baseline_gradient = self._calc_gradient(vals)

            # 確保基線有合理的最小值
            self._baseline_std = max(self._baseline_std, 0.3)
            self._baseline_range = max(self._baseline_range, 0.5)
            self._baseline_transition = max(self._baseline_transition, 0.02)
            self._baseline_gradient = max(self._baseline_gradient, 0.05)

            self._state = MotionState.IDLE
            self.motion_scores.append(0.0)
            self.rssi_stds.append(self._baseline_std)
            self.transition_rates.append(self._baseline_transition)
            self.gradients.append(self._baseline_gradient)
            self.composite_scores.append(0.0)
            self.timestamps.append(now)

        return self._make_event(now, MotionState.CALIBRATING, 0.0, 0.0)

    def _make_event(
        self, timestamp: float, state: MotionState,
        confidence: float, motion_score: float,
        rssi_mean: float = 0.0, rssi_std: float = 0.0,
        rssi_range: float = 0.0,
        transition_rate: float = 0.0,
        gradient: float = 0.0,
        composite_score: float = 0.0,
    ) -> MotionEvent:
        return MotionEvent(
            timestamp=timestamp,
            state=state,
            confidence=confidence,
            rssi_mean=rssi_mean,
            rssi_std=rssi_std,
            rssi_range=rssi_range,
            motion_score=motion_score,
            transition_rate=transition_rate,
            gradient=gradient,
            composite_score=composite_score,
        )
