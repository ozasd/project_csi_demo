"""
Wi-Fi CSI 跌倒偵測 — 圖形化介面。

使用 tkinter 建構即時偵測儀表板，顯示：
- STI 時序圖
- 相似度時序圖
- 偵測狀態指示燈
- 告警記錄列表
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk, messagebox, scrolledtext

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.csi_reader import (
    CSISample,
    NUM_SUBCARRIERS,
    RealtimeCSIReader,
    SimulatedCSIReader,
)
from src.preprocessing import (
    extract_time_window,
    preprocess,
    samples_to_matrix,
)
from src.sti_analyzer import STIAnalyzer
from src.similarity_analyzer import SimilarityAnalyzer, FallTemplateManager
from src.event_detector import EventDetector, DetectionStatus
from src.alert_manager import AlertManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 預設參數 ───────────────────────────────────────────────
DEFAULT_STI_THRESHOLD = 0.22
DEFAULT_SIMILARITY_THRESHOLD = 0.65
DEFAULT_DURATION_THRESHOLD = 5.0
DEFAULT_WINDOW_SECONDS = 2.0
DEFAULT_SAMPLE_RATE = 20.0  # Hz

# ─── 顏色定義 ───────────────────────────────────────────────
COLOR_BG = "#1e1e2e"
COLOR_PANEL = "#2d2d44"
COLOR_TEXT = "#cdd6f4"
COLOR_ACCENT = "#89b4fa"
COLOR_GREEN = "#a6e3a1"
COLOR_YELLOW = "#f9e2af"
COLOR_RED = "#f38ba8"
COLOR_ORANGE = "#fab387"
COLOR_GRID = "#45475a"

STATUS_COLORS = {
    DetectionStatus.NORMAL: COLOR_GREEN,
    DetectionStatus.MOTION_DETECTED: COLOR_YELLOW,
    DetectionStatus.FALL_DETECTED: COLOR_RED,
}
STATUS_LABELS = {
    DetectionStatus.NORMAL: "正常",
    DetectionStatus.MOTION_DETECTED: "偵測到動作",
    DetectionStatus.FALL_DETECTED: "偵測到跌倒！",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wi-Fi CSI 跌倒偵測 GUI")
    parser.add_argument("--com", default="COM3", help="ESP32 序列埠")
    parser.add_argument("--baud", type=int, default=921600, help="ESP32 鮑率")
    parser.add_argument("--simulate", action="store_true", help="使用模擬資料")
    parser.add_argument("--sti-threshold", type=float, default=DEFAULT_STI_THRESHOLD)
    parser.add_argument("--sim-threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    parser.add_argument("--duration-threshold", type=float, default=DEFAULT_DURATION_THRESHOLD)
    return parser.parse_args()


class FallDetectionGUI:
    """跌倒偵測圖形化介面主類別。"""

    MAX_CHART_POINTS = 200  # 圖表最大資料點數

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self._running = False
        self._detection_thread: threading.Thread | None = None

        # 資料緩衝
        self._sti_history: deque[float] = deque(maxlen=self.MAX_CHART_POINTS)
        self._sim_history: deque[float] = deque(maxlen=self.MAX_CHART_POINTS)
        self._time_history: deque[float] = deque(maxlen=self.MAX_CHART_POINTS)
        self._status_history: deque[DetectionStatus] = deque(maxlen=self.MAX_CHART_POINTS)
        self._alert_log: list[dict] = []
        self._current_status = DetectionStatus.NORMAL
        self._current_sti = 0.0
        self._current_sim = 0.0
        self._current_duration = 0.0
        self._start_time = time.time()

        # 建立視窗
        self.root = tk.Tk()
        self.root.title("Wi-Fi CSI 跌倒偵測系統")
        self.root.geometry("1200x800")
        self.root.configure(bg=COLOR_BG)
        self.root.minsize(900, 600)

        self._build_ui()
        self._setup_detection()

    # ─── UI 建構 ────────────────────────────────────────────

    def _build_ui(self) -> None:
        """建構主介面。"""
        # 頂部狀態列
        self._build_status_bar()

        # 中間主區域（左：圖表，右：面板）
        main_frame = tk.Frame(self.root, bg=COLOR_BG)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # 左側：圖表區
        chart_frame = tk.Frame(main_frame, bg=COLOR_BG)
        chart_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_sti_chart(chart_frame)
        self._build_sim_chart(chart_frame)

        # 右側：資訊面板
        info_frame = tk.Frame(main_frame, bg=COLOR_BG, width=350)
        info_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        info_frame.pack_propagate(False)

        self._build_indicator_panel(info_frame)
        self._build_params_panel(info_frame)
        self._build_alert_panel(info_frame)

        # 底部控制列
        self._build_control_bar()

    def _build_status_bar(self) -> None:
        """頂部狀態橫幅。"""
        bar = tk.Frame(self.root, bg=COLOR_PANEL, height=60)
        bar.pack(fill=tk.X, padx=10, pady=10)
        bar.pack_propagate(False)

        title = tk.Label(
            bar, text="Wi-Fi CSI 跌倒偵測系統",
            font=("Microsoft JhengHei UI", 18, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
        )
        title.pack(side=tk.LEFT, padx=20, pady=10)

        mode_text = "模擬模式" if self.args.simulate else f"即時模式 ({self.args.com})"
        self._mode_label = tk.Label(
            bar, text=mode_text,
            font=("Microsoft JhengHei UI", 11),
            bg=COLOR_PANEL, fg=COLOR_ACCENT,
        )
        self._mode_label.pack(side=tk.RIGHT, padx=20, pady=10)

        self._status_indicator = tk.Label(
            bar, text="● 待命中",
            font=("Microsoft JhengHei UI", 13, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
        )
        self._status_indicator.pack(side=tk.RIGHT, padx=20, pady=10)

    def _build_sti_chart(self, parent: tk.Frame) -> None:
        """STI 時序圖。"""
        frame = tk.LabelFrame(
            parent, text=" STI 訊號趨勢指標 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self._sti_canvas = tk.Canvas(frame, bg=COLOR_PANEL, highlightthickness=0)
        self._sti_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_sim_chart(self, parent: tk.Frame) -> None:
        """相似度時序圖。"""
        frame = tk.LabelFrame(
            parent, text=" 跌倒模式相似度 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        self._sim_canvas = tk.Canvas(frame, bg=COLOR_PANEL, highlightthickness=0)
        self._sim_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_indicator_panel(self, parent: tk.Frame) -> None:
        """狀態指示面板。"""
        frame = tk.LabelFrame(
            parent, text=" 偵測狀態 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.X, pady=(0, 10))

        # 大型狀態燈
        self._big_status = tk.Label(
            frame, text="● 待命中",
            font=("Microsoft JhengHei UI", 22, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
        )
        self._big_status.pack(pady=15)

        # 即時數值
        values_frame = tk.Frame(frame, bg=COLOR_PANEL)
        values_frame.pack(fill=tk.X, padx=15, pady=(0, 10))

        self._sti_value_label = self._make_value_row(values_frame, "STI", "0.0000")
        self._sim_value_label = self._make_value_row(values_frame, "相似度", "0.0000")
        self._dur_value_label = self._make_value_row(values_frame, "持續時間", "0.0 秒")

    def _make_value_row(self, parent: tk.Frame, label_text: str, initial: str) -> tk.Label:
        """建立一個「標籤: 數值」的橫列。"""
        row = tk.Frame(parent, bg=COLOR_PANEL)
        row.pack(fill=tk.X, pady=2)

        tk.Label(
            row, text=f"{label_text}：",
            font=("Microsoft JhengHei UI", 10),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
            anchor="w", width=10,
        ).pack(side=tk.LEFT)

        val = tk.Label(
            row, text=initial,
            font=("Consolas", 11, "bold"),
            bg=COLOR_PANEL, fg=COLOR_ACCENT,
            anchor="e",
        )
        val.pack(side=tk.RIGHT)
        return val

    def _build_params_panel(self, parent: tk.Frame) -> None:
        """參數顯示面板。"""
        frame = tk.LabelFrame(
            parent, text=" 偵測參數 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.X, pady=(0, 10))

        params = [
            ("STI 門檻", f"{self.args.sti_threshold:.2f}"),
            ("相似度門檻", f"{self.args.sim_threshold:.2f}"),
            ("持續時間門檻", f"{self.args.duration_threshold:.1f} 秒"),
        ]
        for label, value in params:
            row = tk.Frame(frame, bg=COLOR_PANEL)
            row.pack(fill=tk.X, padx=15, pady=2)
            tk.Label(
                row, text=f"{label}：",
                font=("Microsoft JhengHei UI", 9),
                bg=COLOR_PANEL, fg=COLOR_TEXT,
                anchor="w", width=12,
            ).pack(side=tk.LEFT)
            tk.Label(
                row, text=value,
                font=("Consolas", 9),
                bg=COLOR_PANEL, fg=COLOR_ORANGE,
                anchor="e",
            ).pack(side=tk.RIGHT)

        # 底部留白
        tk.Frame(frame, bg=COLOR_PANEL, height=5).pack()

    def _build_alert_panel(self, parent: tk.Frame) -> None:
        """告警記錄面板。"""
        frame = tk.LabelFrame(
            parent, text=" 告警記錄 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.BOTH, expand=True)

        self._alert_text = scrolledtext.ScrolledText(
            frame,
            font=("Consolas", 9),
            bg="#1a1a2e", fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            selectbackground=COLOR_ACCENT,
            wrap=tk.WORD,
            state=tk.DISABLED,
            height=8,
        )
        self._alert_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_control_bar(self) -> None:
        """底部控制按鈕列。"""
        bar = tk.Frame(self.root, bg=COLOR_BG, height=50)
        bar.pack(fill=tk.X, padx=10, pady=(0, 10))

        self._start_btn = tk.Button(
            bar, text="▶ 開始偵測",
            font=("Microsoft JhengHei UI", 11, "bold"),
            bg=COLOR_GREEN, fg="#1e1e2e",
            activebackground="#7bc98f", activeforeground="#1e1e2e",
            relief=tk.FLAT, padx=20, pady=5,
            command=self._on_start,
        )
        self._start_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._stop_btn = tk.Button(
            bar, text="■ 停止偵測",
            font=("Microsoft JhengHei UI", 11, "bold"),
            bg=COLOR_RED, fg="#1e1e2e",
            activebackground="#d96b8a", activeforeground="#1e1e2e",
            relief=tk.FLAT, padx=20, pady=5,
            state=tk.DISABLED,
            command=self._on_stop,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._clear_btn = tk.Button(
            bar, text="清除記錄",
            font=("Microsoft JhengHei UI", 10),
            bg=COLOR_PANEL, fg=COLOR_TEXT,
            activebackground="#3d3d5c", activeforeground=COLOR_TEXT,
            relief=tk.FLAT, padx=15, pady=5,
            command=self._on_clear,
        )
        self._clear_btn.pack(side=tk.LEFT)

        # 告警計數
        self._alert_count_label = tk.Label(
            bar, text="告警次數: 0",
            font=("Microsoft JhengHei UI", 10),
            bg=COLOR_BG, fg=COLOR_TEXT,
        )
        self._alert_count_label.pack(side=tk.RIGHT, padx=10)

    # ─── 偵測引擎設定 ──────────────────────────────────────

    def _setup_detection(self) -> None:
        """初始化偵測引擎元件。"""
        if self.args.simulate:
            self._sim = SimulatedCSIReader(sample_rate=DEFAULT_SAMPLE_RATE, seed=42)
            template_mgr = FallTemplateManager()
            window_size = int(DEFAULT_WINDOW_SECONDS * DEFAULT_SAMPLE_RATE)
            fall_template = self._sim.build_fall_template(window_size=window_size)
            template_mgr.add_template(fall_template)
            sim_analyzer = SimilarityAnalyzer(
                threshold=self.args.sim_threshold,
                template_manager=template_mgr,
            )
        else:
            self._sim = None
            sim_analyzer = SimilarityAnalyzer(threshold=self.args.sim_threshold)

        self._detector = EventDetector(
            sti_threshold=self.args.sti_threshold,
            similarity_threshold=self.args.sim_threshold,
            duration_threshold=self.args.duration_threshold,
            similarity_analyzer=sim_analyzer,
        )
        self._alert_mgr = AlertManager(
            cooldown_seconds=30.0,
            log_file=str(Path(__file__).resolve().parent.parent / "data" / "fall_alerts.jsonl"),
            enable_sound=True,
        )
        self._alert_mgr.register_callback(self._on_alert_callback)

    # ─── 偵測執行緒 ────────────────────────────────────────

    def _on_start(self) -> None:
        """按下開始偵測。"""
        if self._running:
            return
        self._running = True
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_indicator.config(text="● 偵測中", fg=COLOR_GREEN)

        self._detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._detection_thread.start()
        self._schedule_ui_update()

    def _on_stop(self) -> None:
        """按下停止偵測。"""
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_indicator.config(text="● 已停止", fg=COLOR_TEXT)

    def _on_clear(self) -> None:
        """清除告警記錄。"""
        self._alert_log.clear()
        self._alert_text.config(state=tk.NORMAL)
        self._alert_text.delete("1.0", tk.END)
        self._alert_text.config(state=tk.DISABLED)

    def _on_alert_callback(self, alert_info: dict) -> None:
        """AlertManager 的回呼，在偵測執行緒中被呼叫。"""
        self._alert_log.append(alert_info)

    def _detection_loop(self) -> None:
        """偵測主迴圈（在背景執行緒中執行）。"""
        if self.args.simulate:
            self._run_simulation_loop()
        else:
            self._run_realtime_loop()

    def _run_simulation_loop(self) -> None:
        """模擬資料偵測迴圈。"""
        sim = self._sim
        # 產生三段資料：正常 → 跌倒 → 走動，循環播放
        while self._running:
            normal_data = sim.generate_normal(duration=5.0)
            fall_data = sim.generate_fall_event(pre_seconds=3.0, fall_seconds=1.0, post_seconds=8.0)
            walk_data = sim.generate_walking(duration=5.0)

            all_data = normal_data + fall_data + walk_data
            t0 = all_data[0].timestamp
            for i, sample in enumerate(all_data):
                sample.timestamp = t0 + i / DEFAULT_SAMPLE_RATE

            window_size = int(DEFAULT_WINDOW_SECONDS * DEFAULT_SAMPLE_RATE)
            buffer: deque[CSISample] = deque(maxlen=window_size * 2)

            for sample in all_data:
                if not self._running:
                    return

                buffer.append(sample)
                window = extract_time_window(list(buffer), window_seconds=DEFAULT_WINDOW_SECONDS)
                if len(window) < 2:
                    time.sleep(1.0 / DEFAULT_SAMPLE_RATE)
                    continue

                matrix, timestamps = preprocess(window, smooth_window=3, do_normalize=False)

                status = self._detector.update(
                    subcarrier_amplitudes=sample.amplitudes,
                    amplitude_matrix=matrix,
                    timestamp=sample.timestamp,
                )

                state = self._detector.get_state_dict(timestamp=sample.timestamp)
                elapsed = time.time() - self._start_time

                self._current_status = status
                self._current_sti = state["sti"]
                self._current_sim = state["similarity"]
                self._current_duration = state["high_similarity_duration"]
                self._sti_history.append(state["sti"])
                self._sim_history.append(state["similarity"])
                self._time_history.append(elapsed)
                self._status_history.append(status)

                if status == DetectionStatus.FALL_DETECTED:
                    self._alert_mgr.trigger_alert(state)

                # 模擬取樣率的延遲
                time.sleep(1.0 / DEFAULT_SAMPLE_RATE)

            # 重置偵測器以便下一輪循環
            self._detector.reset()

    def _run_realtime_loop(self) -> None:
        """即時 ESP32 偵測迴圈。"""
        try:
            from src.esp32_csi_reader import ESP32CSISource
        except ImportError:
            logger.error("無法匯入 ESP32CSISource，請確認 esp32_csi_reader.py 存在。")
            return

        try:
            source = ESP32CSISource(port=self.args.com, baudrate=self.args.baud)
            reader = RealtimeCSIReader(source)
            source.start()
        except Exception as exc:
            logger.error("無法連接 ESP32: %s", exc)
            return

        window_size = int(DEFAULT_WINDOW_SECONDS * DEFAULT_SAMPLE_RATE)

        try:
            while self._running:
                samples = reader.read_window(window_size=window_size * 2)
                if len(samples) < 2:
                    time.sleep(0.05)
                    continue

                window = extract_time_window(samples, window_seconds=DEFAULT_WINDOW_SECONDS)
                if len(window) < 2:
                    time.sleep(0.05)
                    continue

                latest = window[-1]
                matrix, timestamps = preprocess(window, smooth_window=3, do_normalize=False)

                status = self._detector.update(
                    subcarrier_amplitudes=latest.amplitudes,
                    amplitude_matrix=matrix,
                    timestamp=latest.timestamp,
                )

                state = self._detector.get_state_dict(timestamp=latest.timestamp)
                elapsed = time.time() - self._start_time

                self._current_status = status
                self._current_sti = state["sti"]
                self._current_sim = state["similarity"]
                self._current_duration = state["high_similarity_duration"]
                self._sti_history.append(state["sti"])
                self._sim_history.append(state["similarity"])
                self._time_history.append(elapsed)
                self._status_history.append(status)

                if status == DetectionStatus.FALL_DETECTED:
                    self._alert_mgr.trigger_alert(state)

                time.sleep(0.05)
        except Exception:
            logger.exception("即時偵測迴圈發生錯誤")
        finally:
            try:
                source.stop()
            except Exception:
                pass

    # ─── UI 更新 ────────────────────────────────────────────

    def _schedule_ui_update(self) -> None:
        """排程 UI 更新。"""
        if not self._running:
            return
        self._update_ui()
        self.root.after(100, self._schedule_ui_update)

    def _update_ui(self) -> None:
        """在主執行緒中更新 UI 元件。"""
        # 更新狀態指示
        status = self._current_status
        color = STATUS_COLORS.get(status, COLOR_TEXT)
        label = STATUS_LABELS.get(status, "未知")
        self._big_status.config(text=f"● {label}", fg=color)
        self._status_indicator.config(text=f"● {label}", fg=color)

        # 更新數值
        self._sti_value_label.config(text=f"{self._current_sti:.4f}")
        self._sim_value_label.config(text=f"{self._current_sim:.4f}")
        self._dur_value_label.config(text=f"{self._current_duration:.1f} 秒")

        # 更新告警計數
        self._alert_count_label.config(
            text=f"告警次數: {self._alert_mgr.alert_count}"
        )

        # 繪製圖表
        self._draw_chart(
            self._sti_canvas,
            list(self._sti_history),
            list(self._time_history),
            threshold=self.args.sti_threshold,
            color=COLOR_ACCENT,
            y_max=1.0,
            label="STI",
        )
        self._draw_chart(
            self._sim_canvas,
            list(self._sim_history),
            list(self._time_history),
            threshold=self.args.sim_threshold,
            color=COLOR_ORANGE,
            y_max=1.0,
            label="相似度",
        )

        # 更新告警記錄
        self._update_alert_text()

    def _draw_chart(
        self,
        canvas: tk.Canvas,
        values: list[float],
        times: list[float],
        threshold: float,
        color: str,
        y_max: float,
        label: str,
    ) -> None:
        """在 Canvas 上繪製時序圖。"""
        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 10 or h < 10:
            return

        margin_l, margin_r, margin_t, margin_b = 55, 15, 10, 25
        plot_w = w - margin_l - margin_r
        plot_h = h - margin_t - margin_b

        if plot_w < 10 or plot_h < 10:
            return

        # 背景
        canvas.create_rectangle(
            margin_l, margin_t,
            margin_l + plot_w, margin_t + plot_h,
            fill="#1a1a2e", outline=COLOR_GRID,
        )

        # Y 軸刻度
        for i in range(5):
            y_val = y_max * i / 4
            y_pos = margin_t + plot_h - (y_val / y_max) * plot_h
            canvas.create_line(
                margin_l, y_pos, margin_l + plot_w, y_pos,
                fill=COLOR_GRID, dash=(2, 4),
            )
            canvas.create_text(
                margin_l - 5, y_pos,
                text=f"{y_val:.2f}",
                fill=COLOR_TEXT, font=("Consolas", 8),
                anchor="e",
            )

        # 門檻線
        th_y = margin_t + plot_h - (threshold / y_max) * plot_h
        canvas.create_line(
            margin_l, th_y, margin_l + plot_w, th_y,
            fill=COLOR_RED, width=1, dash=(4, 4),
        )
        canvas.create_text(
            margin_l + plot_w + 2, th_y,
            text=f"{threshold:.2f}",
            fill=COLOR_RED, font=("Consolas", 7),
            anchor="w",
        )

        # 資料線
        if len(values) < 2:
            return

        points = []
        n = len(values)
        for i, val in enumerate(values):
            x = margin_l + (i / max(n - 1, 1)) * plot_w
            y = margin_t + plot_h - (min(val, y_max) / y_max) * plot_h
            points.append((x, y))

        # 以狀態上色的填充區域
        if len(self._status_history) >= n:
            statuses = list(self._status_history)[-n:]
        else:
            statuses = [DetectionStatus.NORMAL] * n

        # 繪製資料曲線
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            seg_color = STATUS_COLORS.get(statuses[i], color)
            canvas.create_line(x1, y1, x2, y2, fill=seg_color, width=2)

        # X 軸標籤（時間）
        if times:
            t_start = times[0]
            t_end = times[-1]
            for i in range(5):
                t_val = t_start + (t_end - t_start) * i / 4
                x_pos = margin_l + (i / 4) * plot_w
                canvas.create_text(
                    x_pos, margin_t + plot_h + 12,
                    text=f"{t_val:.0f}s",
                    fill=COLOR_TEXT, font=("Consolas", 7),
                )

    def _update_alert_text(self) -> None:
        """更新告警記錄面板。"""
        current_count = int(self._alert_text.index("end-1c").split(".")[0])
        expected = len(self._alert_log) + 1  # text widget starts at line 1

        if len(self._alert_log) == 0:
            return

        # 只追加新的
        new_start = current_count - 1
        if new_start < 0:
            new_start = 0

        if new_start >= len(self._alert_log):
            return

        self._alert_text.config(state=tk.NORMAL)
        for alert in self._alert_log[new_start:]:
            ts = alert.get("alert_time", "N/A")
            sti = alert.get("sti", 0)
            sim = alert.get("similarity", 0)
            dur = alert.get("high_similarity_duration", 0)
            line = (
                f"⚠ [{ts}]\n"
                f"  STI={sti:.4f}  相似度={sim:.4f}  "
                f"持續={dur:.1f}秒\n\n"
            )
            self._alert_text.insert(tk.END, line)
        self._alert_text.see(tk.END)
        self._alert_text.config(state=tk.DISABLED)

    # ─── 啟動 ──────────────────────────────────────────────

    def run(self) -> None:
        """啟動 GUI 主迴圈。"""
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        """視窗關閉。"""
        self._running = False
        if self._detection_thread and self._detection_thread.is_alive():
            self._detection_thread.join(timeout=2.0)
        self.root.destroy()


def main() -> None:
    args = parse_args()
    app = FallDetectionGUI(args)
    app.run()


if __name__ == "__main__":
    main()
