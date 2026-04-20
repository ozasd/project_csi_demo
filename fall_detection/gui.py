"""
Wi-Fi CSI 跌倒偵測 — 圖形化介面。

使用 tkinter 建構即時偵測儀表板，顯示：
- STI 時序圖
- 相似度時序圖
- 偵測狀態指示燈
- 代理動作資料錄製面板
- 告警記錄列表
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext

import numpy as np

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import config as cfg
from src.alert_manager import AlertManager
from src.csi_reader import (
    CSISample,
    CSVCSIReader,
    RealtimeCSIReader,
    SimulatedCSIReader,
    save_samples_to_csv,
)
from src.event_detector import DetectionStatus, EventDetector
from src.preprocessing import extract_time_window, preprocess
from src.similarity_analyzer import FallTemplateManager, SimilarityAnalyzer
from src.sti_analyzer import compute_sti_series
from src.voice_notifier import VoiceNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 預設參數 ───────────────────────────────────────────────
DEFAULT_STI_THRESHOLD = 0.22
DEFAULT_SIMILARITY_THRESHOLD = 0.65
DEFAULT_DURATION_THRESHOLD = 3.0
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

    MAX_CHART_POINTS = 200
    UI_UPDATE_MS = 100
    CSI_RECENT_SECONDS = 1.0

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
        self._template_count = 0
        self._adl_template_count = 0
        self._template_load_errors: list[str] = []
        self._last_announced_status = DetectionStatus.NORMAL

        # 即時來源狀態
        self._source = None
        self._reader = None
        self._last_csi_wall_time = 0.0
        self._last_sample_timestamp = 0.0

        # 錄製狀態
        self._record_lock = threading.Lock()
        self._recording_pending = False
        self._recording_active = False
        self._recording_countdown_deadline = 0.0
        self._recording_started_at = 0.0
        self._recording_elapsed = 0.0
        self._recording_samples: list[CSISample] = []
        self._recording_label = cfg.RECORDING_LABELS[0]
        self._recording_last_timestamp = -1.0
        self._recording_status_text = "待命"
        self._recording_status_color = COLOR_TEXT
        self._recording_result_text = "尚未錄製"
        self._pending_warning_message: str | None = None
        self._voice_notifier: VoiceNotifier | None = None
        self._sim_analyzer: SimilarityAnalyzer | None = None
        self._adl_analyzer: SimilarityAnalyzer | None = None

        # 建立視窗
        self.root = tk.Tk()
        self.root.title("Wi-Fi CSI 跌倒偵測系統")
        self.root.geometry("1220x860")
        self.root.configure(bg=COLOR_BG)
        self.root.minsize(980, 680)

        self._record_label_var = tk.StringVar(value=cfg.RECORDING_LABELS[0])
        self._record_label_buttons: list[tk.Radiobutton] = []

        self._build_ui()
        self._setup_detection()

    # ─── UI 建構 ────────────────────────────────────────────

    def _build_ui(self) -> None:
        """建構主介面。"""
        self._build_status_bar()

        main_frame = tk.Frame(self.root, bg=COLOR_BG)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        chart_frame = tk.Frame(main_frame, bg=COLOR_BG)
        chart_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_sti_chart(chart_frame)
        self._build_sim_chart(chart_frame)

        info_frame = tk.Frame(main_frame, bg=COLOR_BG, width=380)
        info_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        info_frame.pack_propagate(False)

        self._build_indicator_panel(info_frame)
        self._build_params_panel(info_frame)
        self._build_recording_panel(info_frame)
        self._build_alert_panel(info_frame)

        self._build_control_bar()
        self._update_recording_widgets()

    def _build_status_bar(self) -> None:
        """頂部狀態橫幅。"""
        bar = tk.Frame(self.root, bg=COLOR_PANEL, height=60)
        bar.pack(fill=tk.X, padx=10, pady=10)
        bar.pack_propagate(False)

        title = tk.Label(
            bar,
            text="Wi-Fi CSI 跌倒偵測系統",
            font=("Microsoft JhengHei UI", 18, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
        )
        title.pack(side=tk.LEFT, padx=20, pady=10)

        mode_text = "模擬模式" if self.args.simulate else f"即時模式 ({self.args.com})"
        self._mode_label = tk.Label(
            bar,
            text=mode_text,
            font=("Microsoft JhengHei UI", 11),
            bg=COLOR_PANEL,
            fg=COLOR_ACCENT,
        )
        self._mode_label.pack(side=tk.RIGHT, padx=20, pady=10)

        self._status_indicator = tk.Label(
            bar,
            text="● 待命中",
            font=("Microsoft JhengHei UI", 13, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
        )
        self._status_indicator.pack(side=tk.RIGHT, padx=20, pady=10)

    def _build_sti_chart(self, parent: tk.Frame) -> None:
        """STI 時序圖。"""
        frame = tk.LabelFrame(
            parent,
            text=" STI 訊號趨勢指標 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self._sti_canvas = tk.Canvas(frame, bg=COLOR_PANEL, highlightthickness=0)
        self._sti_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_sim_chart(self, parent: tk.Frame) -> None:
        """相似度時序圖。"""
        frame = tk.LabelFrame(
            parent,
            text=" 跌倒模式相似度 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        self._sim_canvas = tk.Canvas(frame, bg=COLOR_PANEL, highlightthickness=0)
        self._sim_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_indicator_panel(self, parent: tk.Frame) -> None:
        """狀態指示面板。"""
        frame = tk.LabelFrame(
            parent,
            text=" 偵測狀態 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.X, pady=(0, 10))

        self._big_status = tk.Label(
            frame,
            text="● 待命中",
            font=("Microsoft JhengHei UI", 22, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
        )
        self._big_status.pack(pady=15)

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
            row,
            text=f"{label_text}：",
            font=("Microsoft JhengHei UI", 10),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            anchor="w",
            width=10,
        ).pack(side=tk.LEFT)

        value_label = tk.Label(
            row,
            text=initial,
            font=("Consolas", 11, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_ACCENT,
            anchor="e",
        )
        value_label.pack(side=tk.RIGHT)
        return value_label

    def _build_params_panel(self, parent: tk.Frame) -> None:
        """參數顯示面板。"""
        frame = tk.LabelFrame(
            parent,
            text=" 偵測參數 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
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
                row,
                text=f"{label}：",
                font=("Microsoft JhengHei UI", 9),
                bg=COLOR_PANEL,
                fg=COLOR_TEXT,
                anchor="w",
                width=12,
            ).pack(side=tk.LEFT)
            tk.Label(
                row,
                text=value,
                font=("Consolas", 9),
                bg=COLOR_PANEL,
                fg=COLOR_ORANGE,
                anchor="e",
            ).pack(side=tk.RIGHT)

        tk.Frame(frame, bg=COLOR_PANEL, height=5).pack()

    def _build_recording_panel(self, parent: tk.Frame) -> None:
        """代理動作資料錄製面板。"""
        frame = tk.LabelFrame(
            parent,
            text=" 資料錄製 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.X, pady=(0, 10))

        values_frame = tk.Frame(frame, bg=COLOR_PANEL)
        values_frame.pack(fill=tk.X, padx=15, pady=(10, 6))
        self._record_source_value = self._make_value_row(values_frame, "來源", self.args.com)
        self._record_csi_value = self._make_value_row(values_frame, "CSI 狀態", "請先開始偵測")
        self._record_state_value = self._make_value_row(values_frame, "錄製狀態", "待命")
        self._record_elapsed_value = self._make_value_row(values_frame, "已錄時間", "0.0 秒 / 0 筆")

        label_frame = tk.Frame(frame, bg=COLOR_PANEL)
        label_frame.pack(fill=tk.X, padx=15, pady=(4, 0))
        tk.Label(
            label_frame,
            text="標籤：",
            font=("Microsoft JhengHei UI", 10),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
        ).pack(side=tk.LEFT)

        for label in cfg.RECORDING_LABELS:
            radio = tk.Radiobutton(
                label_frame,
                text=label,
                variable=self._record_label_var,
                value=label,
                font=("Consolas", 10),
                bg=COLOR_PANEL,
                fg=COLOR_TEXT,
                selectcolor="#1a1a2e",
                activebackground=COLOR_PANEL,
                activeforeground=COLOR_TEXT,
                highlightthickness=0,
            )
            radio.pack(side=tk.LEFT, padx=(6, 0))
            self._record_label_buttons.append(radio)

        action_frame = tk.Frame(frame, bg=COLOR_PANEL)
        action_frame.pack(fill=tk.X, padx=15, pady=(10, 6))

        self._record_start_btn = tk.Button(
            action_frame,
            text="● 開始錄製",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_ORANGE,
            fg="#1e1e2e",
            activebackground="#f1a95f",
            activeforeground="#1e1e2e",
            relief=tk.FLAT,
            padx=14,
            pady=4,
            command=self._on_start_recording,
        )
        self._record_start_btn.pack(side=tk.LEFT)

        self._record_stop_btn = tk.Button(
            action_frame,
            text="■ 停止錄製",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_RED,
            fg="#1e1e2e",
            activebackground="#d96b8a",
            activeforeground="#1e1e2e",
            relief=tk.FLAT,
            padx=14,
            pady=4,
            command=self._on_stop_recording,
        )
        self._record_stop_btn.pack(side=tk.LEFT, padx=(8, 0))

        hint = (
            f"開始錄製後會先倒數 {cfg.RECORD_COUNTDOWN_SECONDS} 秒。\n"
            "建議每段手動錄 6-8 秒；proxy_fall 錄快速倒地/側躺，adl 錄走路、坐下、蹲下等日常動作。"
        )
        self._record_hint_label = tk.Label(
            frame,
            text=hint,
            font=("Microsoft JhengHei UI", 8),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            justify=tk.LEFT,
            wraplength=320,
        )
        self._record_hint_label.pack(fill=tk.X, padx=15, pady=(0, 8))

        self._record_result_label = tk.Label(
            frame,
            text="尚未錄製",
            font=("Microsoft JhengHei UI", 8),
            bg=COLOR_PANEL,
            fg=COLOR_ACCENT,
            justify=tk.LEFT,
            anchor="w",
            wraplength=320,
        )
        self._record_result_label.pack(fill=tk.X, padx=15, pady=(0, 10))

    def _build_alert_panel(self, parent: tk.Frame) -> None:
        """告警記錄面板。"""
        frame = tk.LabelFrame(
            parent,
            text=" 告警記錄 ",
            font=("Microsoft JhengHei UI", 10, "bold"),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            labelanchor="nw",
        )
        frame.pack(fill=tk.BOTH, expand=True)

        self._alert_text = scrolledtext.ScrolledText(
            frame,
            font=("Consolas", 9),
            bg="#1a1a2e",
            fg=COLOR_TEXT,
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
            bar,
            text="▶ 開始偵測",
            font=("Microsoft JhengHei UI", 11, "bold"),
            bg=COLOR_GREEN,
            fg="#1e1e2e",
            activebackground="#7bc98f",
            activeforeground="#1e1e2e",
            relief=tk.FLAT,
            padx=20,
            pady=5,
            command=self._on_start,
        )
        self._start_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._stop_btn = tk.Button(
            bar,
            text="■ 停止偵測",
            font=("Microsoft JhengHei UI", 11, "bold"),
            bg=COLOR_RED,
            fg="#1e1e2e",
            activebackground="#d96b8a",
            activeforeground="#1e1e2e",
            relief=tk.FLAT,
            padx=20,
            pady=5,
            state=tk.DISABLED,
            command=self._on_stop,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._clear_btn = tk.Button(
            bar,
            text="清除記錄",
            font=("Microsoft JhengHei UI", 10),
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            activebackground="#3d3d5c",
            activeforeground=COLOR_TEXT,
            relief=tk.FLAT,
            padx=15,
            pady=5,
            command=self._on_clear,
        )
        self._clear_btn.pack(side=tk.LEFT)

        self._alert_count_label = tk.Label(
            bar,
            text="告警次數: 0",
            font=("Microsoft JhengHei UI", 10),
            bg=COLOR_BG,
            fg=COLOR_TEXT,
        )
        self._alert_count_label.pack(side=tk.RIGHT, padx=10)

    # ─── 偵測引擎設定 ──────────────────────────────────────

    def _setup_detection(self) -> None:
        """初始化偵測引擎元件。"""
        self._voice_notifier = VoiceNotifier(
            fall_text=cfg.VOICE_FALL_TEXT,
            motion_text=cfg.VOICE_MOTION_TEXT,
            fall_cooldown=cfg.VOICE_FALL_COOLDOWN,
            motion_cooldown=cfg.VOICE_MOTION_COOLDOWN,
            enabled=not self.args.simulate,
        )

        if self.args.simulate:
            self._sim = SimulatedCSIReader(sample_rate=DEFAULT_SAMPLE_RATE, seed=42)
            template_mgr = FallTemplateManager()
            window_size = int(DEFAULT_WINDOW_SECONDS * DEFAULT_SAMPLE_RATE)
            fall_template = self._sim.build_fall_template(window_size=window_size)
            template_mgr.add_template(fall_template)
            self._sim_analyzer = SimilarityAnalyzer(
                threshold=self.args.sim_threshold,
                template_manager=template_mgr,
            )
            self._adl_analyzer = None
        else:
            self._sim = None
            self._sim_analyzer = SimilarityAnalyzer(
                threshold=self.args.sim_threshold,
                template_manager=FallTemplateManager(),
            )
            self._adl_analyzer = SimilarityAnalyzer(
                threshold=self.args.sim_threshold,
                template_manager=FallTemplateManager(),
            )
            self._reload_recording_templates()
            self._recording_result_text = self._format_template_status()

        self._detector = EventDetector(
            sti_threshold=self.args.sti_threshold,
            similarity_threshold=self.args.sim_threshold,
            duration_threshold=self.args.duration_threshold,
            similarity_analyzer=self._sim_analyzer,
            adl_similarity_analyzer=self._adl_analyzer,
            similarity_margin=cfg.TEMPLATE_SIMILARITY_MARGIN,
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

        if not self.args.simulate:
            self._reload_recording_templates()

        self._running = True
        self._start_time = time.time()
        self._last_announced_status = DetectionStatus.NORMAL
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_indicator.config(text="● 偵測中", fg=COLOR_GREEN)
        self._recording_result_text = self._format_template_status()

        self._detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._detection_thread.start()
        self._schedule_ui_update()
        self._update_recording_widgets()

    def _on_stop(self) -> None:
        """按下停止偵測。"""
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_indicator.config(text="● 已停止", fg=COLOR_TEXT)
        self._stop_recording(
            reason="偵測已停止，已儲存目前錄製資料。",
            interrupted=False,
            save_if_samples=True,
        )
        self._update_recording_widgets()

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
        try:
            if self.args.simulate:
                self._run_simulation_loop()
            else:
                self._run_realtime_loop()
        finally:
            if self._running:
                self.root.after(0, self._handle_detection_thread_exit)

    def _run_simulation_loop(self) -> None:
        """模擬資料偵測迴圈。"""
        sim = self._sim
        while self._running:
            normal_data = sim.generate_normal(duration=5.0)
            fall_data = sim.generate_fall_event(
                pre_seconds=3.0,
                fall_seconds=1.0,
                post_seconds=8.0,
            )
            walk_data = sim.generate_walking(duration=5.0)

            all_data = normal_data + fall_data + walk_data
            t0 = all_data[0].timestamp
            for idx, sample in enumerate(all_data):
                sample.timestamp = t0 + idx / DEFAULT_SAMPLE_RATE

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

                matrix, _ = preprocess(window, smooth_window=3, do_normalize=False)

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
                if self._detector.similarity_available:
                    self._sim_history.append(state["similarity"])
                self._time_history.append(elapsed)
                self._status_history.append(status)
                self._announce_status(status)

                if status == DetectionStatus.FALL_DETECTED:
                    self._alert_mgr.trigger_alert(state)

                time.sleep(1.0 / DEFAULT_SAMPLE_RATE)

            self._detector.reset()

    def _run_realtime_loop(self) -> None:
        """即時 ESP32 偵測迴圈。"""
        try:
            from src.esp32_csi_reader import ESP32CSISource
        except ImportError:
            logger.error("無法匯入 ESP32CSISource，請確認 esp32_csi_reader.py 存在。")
            return

        source = None
        try:
            source = ESP32CSISource(port=self.args.com, baudrate=self.args.baud)
            reader = RealtimeCSIReader(source)
            self._source = source
            self._reader = reader
            source.start()
        except Exception as exc:
            logger.error("無法連接 ESP32: %s", exc)
            self._queue_warning("無法連接 ESP32，請確認序列埠與裝置狀態。")
            return

        window_size = int(DEFAULT_WINDOW_SECONDS * DEFAULT_SAMPLE_RATE)

        try:
            while self._running:
                samples = reader.read_window(window_size=window_size * 2)
                latest = samples[-1] if samples else None

                if latest is None:
                    self._handle_stream_gap()
                    time.sleep(0.05)
                    continue

                self._handle_realtime_sample(latest)

                if len(samples) < 2:
                    time.sleep(0.05)
                    continue

                window = extract_time_window(samples, window_seconds=DEFAULT_WINDOW_SECONDS)
                if len(window) < 2:
                    time.sleep(0.05)
                    continue

                matrix, _ = preprocess(window, smooth_window=3, do_normalize=False)

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
                if self._detector.similarity_available:
                    self._sim_history.append(state["similarity"])
                self._time_history.append(elapsed)
                self._status_history.append(status)
                self._announce_status(status)

                if status == DetectionStatus.FALL_DETECTED:
                    self._alert_mgr.trigger_alert(state)

                time.sleep(0.05)
        except Exception:
            logger.exception("即時偵測迴圈發生錯誤")
            self._queue_warning("即時偵測迴圈發生錯誤，若正在錄製，這段資料可能不完整。")
        finally:
            self._stop_recording(
                reason="CSI 串流已停止，已儲存目前錄製資料。",
                interrupted=False,
                save_if_samples=True,
            )
            if source is not None:
                try:
                    source.stop()
                except Exception:
                    pass
            self._source = None
            self._reader = None

    def _handle_detection_thread_exit(self) -> None:
        """當偵測執行緒非預期結束時，回收 UI 狀態。"""
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_indicator.config(text="● 已停止", fg=COLOR_TEXT)
        self._update_recording_widgets()

    # ─── 錄製控制 ──────────────────────────────────────────

    def _on_start_recording(self) -> None:
        """開始代理動作錄製，先倒數再收集 CSI。"""
        if self.args.simulate:
            messagebox.showinfo("錄製不可用", "模擬模式只用於 UI/流程測試，不支援真實資料錄製。")
            return
        if not self._running:
            messagebox.showwarning("尚未開始偵測", "請先開始偵測，並確認已收到 CSI 資料。")
            return
        if not self._has_recent_csi():
            messagebox.showwarning("尚未收到 CSI", "目前還沒有收到即時 CSI 資料，請確認 ESP32 已連線並正在輸出。")
            return

        with self._record_lock:
            if self._recording_pending or self._recording_active:
                return
            self._recording_pending = True
            self._recording_active = False
            self._recording_countdown_deadline = time.time() + cfg.RECORD_COUNTDOWN_SECONDS
            self._recording_started_at = 0.0
            self._recording_elapsed = 0.0
            self._recording_samples = []
            self._recording_label = self._record_label_var.get()
            self._recording_last_timestamp = -1.0
            self._recording_status_text = f"倒數 {cfg.RECORD_COUNTDOWN_SECONDS} 秒後開始"
            self._recording_status_color = COLOR_YELLOW
            self._recording_result_text = (
                f"即將錄製 {self._recording_label}，請在倒數結束後進行動作。"
            )

        self._update_recording_widgets()

    def _on_stop_recording(self) -> None:
        """手動停止錄製。"""
        self._stop_recording(
            reason="已手動停止錄製。",
            interrupted=False,
            save_if_samples=True,
        )
        self._update_recording_widgets()

    def _handle_realtime_sample(self, sample: CSISample) -> None:
        """收到新 CSI 取樣後，更新即時狀態與錄製緩衝。"""
        self._last_csi_wall_time = time.time()
        self._last_sample_timestamp = sample.timestamp
        self._process_recording_sample(sample)

    def _handle_stream_gap(self) -> None:
        """若即時 CSI 串流中斷，安全終止目前錄製。"""
        if not self._is_recording_in_progress():
            return
        if self._has_recent_csi():
            return
        self._stop_recording(
            reason="CSI 串流中斷，已停止錄製；這段資料可能不完整。",
            interrupted=True,
            save_if_samples=True,
        )

    def _process_recording_sample(self, sample: CSISample) -> None:
        """根據倒數與錄製狀態，決定是否收下最新樣本。"""
        with self._record_lock:
            now = time.time()
            if self._recording_pending and now >= self._recording_countdown_deadline:
                self._recording_pending = False
                self._recording_active = True
                self._recording_started_at = sample.timestamp
                self._recording_elapsed = 0.0
                self._recording_samples = []
                self._recording_last_timestamp = -1.0
                self._recording_status_text = f"錄製中（{self._recording_label}）"
                self._recording_status_color = COLOR_GREEN

            if not self._recording_active:
                return

            if sample.timestamp <= self._recording_last_timestamp + 1e-9:
                if self._recording_started_at:
                    self._recording_elapsed = max(0.0, sample.timestamp - self._recording_started_at)
                return

            self._recording_samples.append(self._copy_sample(sample))
            self._recording_last_timestamp = sample.timestamp
            self._recording_elapsed = max(0.0, sample.timestamp - self._recording_started_at)

    def _stop_recording(
        self,
        reason: str,
        interrupted: bool,
        save_if_samples: bool,
    ) -> None:
        """結束目前錄製，必要時儲存為 CSV。"""
        with self._record_lock:
            was_pending = self._recording_pending
            was_active = self._recording_active
            if not was_pending and not was_active:
                return

            label = self._recording_label
            samples = [self._copy_sample(sample) for sample in self._recording_samples]
            self._recording_pending = False
            self._recording_active = False
            self._recording_countdown_deadline = 0.0
            self._recording_started_at = 0.0
            self._recording_elapsed = 0.0
            self._recording_last_timestamp = -1.0
            self._recording_samples = []

        if was_pending and not samples:
            with self._record_lock:
                self._recording_status_text = "已取消"
                self._recording_status_color = COLOR_TEXT
                self._recording_result_text = reason
            if interrupted:
                self._queue_warning(reason)
            return

        if samples and save_if_samples:
            try:
                saved_path = self._save_recording_samples(samples, label)
                message = f"{reason}\n已儲存 {len(samples)} 筆到 {saved_path}"
                if label in cfg.RECORDING_LABELS and not self.args.simulate:
                    fall_count, adl_count = self._reload_recording_templates()
                    message += f"\n已重新載入 {fall_count} 個跌倒模板 / {adl_count} 個日常模板"
                with self._record_lock:
                    self._recording_status_text = "已儲存"
                    self._recording_status_color = COLOR_ACCENT
                    self._recording_result_text = message
            except OSError as exc:
                logger.exception("Failed to save recording CSV")
                error_message = f"錄製已停止，但儲存失敗：{exc}"
                with self._record_lock:
                    self._recording_status_text = "儲存失敗"
                    self._recording_status_color = COLOR_RED
                    self._recording_result_text = error_message
                self._queue_warning(error_message)
                return
        else:
            with self._record_lock:
                self._recording_status_text = "已取消"
                self._recording_status_color = COLOR_TEXT
                self._recording_result_text = reason

        if interrupted:
            self._queue_warning(reason)

    def _save_recording_samples(self, samples: list[CSISample], label: str) -> Path:
        """將目前錄製的樣本存成可由 CSVCSIReader 讀取的 CSV。"""
        recordings_root = Path(__file__).resolve().parent / cfg.RECORDINGS_DIR / label
        recordings_root.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        source_name = self.args.com if not self.args.simulate else "SIM"
        safe_source = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in source_name
        )
        filename = f"{timestamp}_{safe_source}.csv"
        filepath = recordings_root / filename

        suffix = 1
        while filepath.exists():
            filepath = recordings_root / f"{timestamp}_{safe_source}_{suffix}.csv"
            suffix += 1

        return save_samples_to_csv(samples, filepath)

    def _copy_sample(self, sample: CSISample) -> CSISample:
        """建立可安全保存的 CSI 取樣副本。"""
        return CSISample(
            timestamp=sample.timestamp,
            amplitudes=sample.amplitudes.copy(),
            rssi_dbm=sample.rssi_dbm,
        )

    def _build_event_matrix_from_samples(self, samples: list[CSISample]):
        """從整段錄製資料中自動找出 STI 峰值，切出事件窗口矩陣。"""
        if len(samples) < 3:
            return None

        matrix, timestamps = preprocess(samples, smooth_window=3, do_normalize=False)
        if matrix.shape[1] < 3:
            return None

        sti_series = compute_sti_series(matrix)
        if sti_series.size == 0:
            return None

        peak_idx = int(np.argmax(sti_series))
        peak_ts = float(timestamps[peak_idx])
        start_ts = peak_ts - cfg.TEMPLATE_PEAK_PRE_SECONDS
        end_ts = peak_ts + cfg.TEMPLATE_PEAK_POST_SECONDS

        event_samples = [
            sample for sample in samples
            if start_ts <= sample.timestamp <= end_ts
        ]
        if len(event_samples) < 3:
            event_samples = samples

        event_matrix, _ = preprocess(event_samples, smooth_window=3, do_normalize=False)
        if event_matrix.shape[1] < 3:
            return None
        return event_matrix

    def _reload_recording_templates(self) -> tuple[int, int]:
        """重新載入 proxy_fall 與 adl 錄製檔，建立模板庫。"""
        self._template_load_errors = []
        fall_count = self._reload_templates_for_label(self._sim_analyzer, "proxy_fall")
        adl_count = self._reload_templates_for_label(self._adl_analyzer, "adl")
        self._template_count = fall_count
        self._adl_template_count = adl_count
        return fall_count, adl_count

    def _reload_templates_for_label(
        self,
        analyzer: SimilarityAnalyzer | None,
        label: str,
    ) -> int:
        """將指定資料夾下的錄製檔切成事件窗口並加入模板庫。"""
        if analyzer is None:
            return 0

        template_manager = analyzer.template_manager
        template_manager.clear_templates()

        recording_dir = Path(__file__).resolve().parent / cfg.RECORDINGS_DIR / label
        loaded_count = 0
        if recording_dir.is_dir():
            for path in sorted(recording_dir.glob("*.csv")):
                try:
                    samples = CSVCSIReader(str(path)).samples
                    matrix = self._build_event_matrix_from_samples(samples)
                    if matrix is None:
                        continue
                    template_manager.add_template(matrix)
                    loaded_count += 1
                except Exception as exc:
                    self._template_load_errors.append(f"{label}/{path.name}: {exc}")
                    logger.exception("Failed to load %s template: %s", label, path)

        logger.info("Loaded %d %s templates from %s", loaded_count, label, recording_dir)
        return loaded_count

    def _format_template_status(self) -> str:
        """將目前模板庫狀態整理成可顯示字串。"""
        if self.args.simulate:
            return "模擬模式使用內建模板"

        parts = []
        if self._template_count > 0:
            parts.append(f"已載入 {self._template_count} 個跌倒模板")
        else:
            parts.append("尚未載入跌倒模板，請先錄製 proxy_fall 資料。")

        if self._adl_template_count > 0:
            parts.append(f"已載入 {self._adl_template_count} 個日常模板")
        else:
            parts.append("尚未載入日常模板，建議再錄製 adl 資料。")

        if self._template_load_errors:
            parts.append(f"模板載入失敗 {len(self._template_load_errors)} 筆，請查看 CMD 訊息。")

        return "\n".join(parts)

    def _announce_status(self, status: DetectionStatus) -> None:
        """依狀態轉換播放一次語音提示。"""
        if self._voice_notifier is None:
            return
        if not self._detector.similarity_available:
            return
        if status == DetectionStatus.MOTION_DETECTED and not self._detector.adl_similarity_available:
            return
        if status == self._last_announced_status:
            return

        if status == DetectionStatus.FALL_DETECTED:
            self._voice_notifier.speak_fall()
        elif status == DetectionStatus.MOTION_DETECTED:
            self._voice_notifier.speak_motion()

        self._last_announced_status = status

    def _is_recording_in_progress(self) -> bool:
        """是否正處於倒數或錄製中。"""
        with self._record_lock:
            return self._recording_pending or self._recording_active

    def _has_recent_csi(self) -> bool:
        """最近是否仍有 CSI 串流進來。"""
        return (time.time() - self._last_csi_wall_time) < self.CSI_RECENT_SECONDS

    def _queue_warning(self, message: str) -> None:
        """將警示訊息排入主執行緒顯示。"""
        with self._record_lock:
            if self._pending_warning_message is None:
                self._pending_warning_message = message

    def _flush_pending_warning(self) -> None:
        """在主執行緒中顯示待處理警示。"""
        with self._record_lock:
            message = self._pending_warning_message
            self._pending_warning_message = None
        if message:
            messagebox.showwarning("錄製提醒", message)

    def _update_recording_widgets(self) -> None:
        """同步錄製面板狀態。"""
        now = time.time()
        with self._record_lock:
            pending = self._recording_pending
            active = self._recording_active
            deadline = self._recording_countdown_deadline
            status_text = self._recording_status_text
            status_color = self._recording_status_color
            elapsed = self._recording_elapsed
            sample_count = len(self._recording_samples)
            result_text = self._recording_result_text

        source_text = "模擬模式" if self.args.simulate else self.args.com
        if self.args.simulate:
            csi_text = "模擬資料，不支援錄製"
            csi_color = COLOR_YELLOW
        elif not self._running:
            csi_text = "請先開始偵測"
            csi_color = COLOR_TEXT
        elif self._has_recent_csi():
            csi_text = "已收到 CSI"
            csi_color = COLOR_GREEN
        else:
            csi_text = "等待 CSI 資料"
            csi_color = COLOR_YELLOW

        if pending:
            remaining = max(0.0, deadline - now)
            status_text = f"倒數 {math.ceil(remaining) if remaining > 0 else 0} 秒"
            status_color = COLOR_YELLOW
        elif active:
            status_text = "錄製中"
            status_color = COLOR_GREEN

        elapsed_text = f"{elapsed:.1f} 秒 / {sample_count} 筆"

        self._record_source_value.config(text=source_text)
        self._record_csi_value.config(text=csi_text, fg=csi_color)
        self._record_state_value.config(text=status_text, fg=status_color)
        self._record_elapsed_value.config(text=elapsed_text, fg=COLOR_ACCENT)
        self._record_result_label.config(text=result_text)

        can_start_recording = (
            not self.args.simulate
            and self._running
            and self._has_recent_csi()
            and not pending
            and not active
        )
        self._record_start_btn.config(state=tk.NORMAL if can_start_recording else tk.DISABLED)
        self._record_stop_btn.config(state=tk.NORMAL if (pending or active) else tk.DISABLED)

        radio_state = tk.DISABLED if self.args.simulate or pending or active else tk.NORMAL
        for radio in self._record_label_buttons:
            radio.config(state=radio_state)

    # ─── UI 更新 ────────────────────────────────────────────

    def _schedule_ui_update(self) -> None:
        """排程 UI 更新。"""
        if not self._running:
            self._update_recording_widgets()
            self._flush_pending_warning()
            return
        self._update_ui()
        self.root.after(self.UI_UPDATE_MS, self._schedule_ui_update)

    def _update_ui(self) -> None:
        """在主執行緒中更新 UI 元件。"""
        status = self._current_status
        color = STATUS_COLORS.get(status, COLOR_TEXT)
        label = STATUS_LABELS.get(status, "未知")
        self._big_status.config(text=f"● {label}", fg=color)
        self._status_indicator.config(text=f"● {label}", fg=color)

        self._sti_value_label.config(text=f"{self._current_sti:.4f}")
        self._dur_value_label.config(text=f"{self._current_duration:.1f} 秒")
        self._alert_count_label.config(text=f"告警次數: {self._alert_mgr.alert_count}")

        if self._detector.similarity_available:
            self._sim_value_label.config(text=f"{self._current_sim:.4f}")
        else:
            self._sim_value_label.config(text="N/A")
            self._sim_history.clear()

        self._draw_chart(
            self._sti_canvas,
            list(self._sti_history),
            list(self._time_history),
            threshold=self.args.sti_threshold,
            color=COLOR_ACCENT,
            y_max=1.0,
        )
        if self._detector.similarity_available:
            self._draw_chart(
                self._sim_canvas,
                list(self._sim_history),
                list(self._time_history),
                threshold=self.args.sim_threshold,
                color=COLOR_ORANGE,
                y_max=1.0,
            )
        else:
            self._draw_na_chart(self._sim_canvas, "N/A\n無跌倒模板")

        self._update_alert_text()
        self._update_recording_widgets()
        self._flush_pending_warning()

    def _draw_chart(
        self,
        canvas: tk.Canvas,
        values: list[float],
        times: list[float],
        threshold: float,
        color: str,
        y_max: float,
    ) -> None:
        """在 Canvas 上繪製時序圖。"""
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 10 or height < 10:
            return

        margin_l, margin_r, margin_t, margin_b = 55, 15, 10, 25
        plot_w = width - margin_l - margin_r
        plot_h = height - margin_t - margin_b

        if plot_w < 10 or plot_h < 10:
            return

        canvas.create_rectangle(
            margin_l,
            margin_t,
            margin_l + plot_w,
            margin_t + plot_h,
            fill="#1a1a2e",
            outline=COLOR_GRID,
        )

        for idx in range(5):
            y_val = y_max * idx / 4
            y_pos = margin_t + plot_h - (y_val / y_max) * plot_h
            canvas.create_line(
                margin_l,
                y_pos,
                margin_l + plot_w,
                y_pos,
                fill=COLOR_GRID,
                dash=(2, 4),
            )
            canvas.create_text(
                margin_l - 5,
                y_pos,
                text=f"{y_val:.2f}",
                fill=COLOR_TEXT,
                font=("Consolas", 8),
                anchor="e",
            )

        th_y = margin_t + plot_h - (threshold / y_max) * plot_h
        canvas.create_line(
            margin_l,
            th_y,
            margin_l + plot_w,
            th_y,
            fill=COLOR_RED,
            width=1,
            dash=(4, 4),
        )
        canvas.create_text(
            margin_l + plot_w + 2,
            th_y,
            text=f"{threshold:.2f}",
            fill=COLOR_RED,
            font=("Consolas", 7),
            anchor="w",
        )

        if len(values) < 2:
            return

        points = []
        n_values = len(values)
        for idx, value in enumerate(values):
            x = margin_l + (idx / max(n_values - 1, 1)) * plot_w
            y = margin_t + plot_h - (min(value, y_max) / y_max) * plot_h
            points.append((x, y))

        if len(self._status_history) >= n_values:
            statuses = list(self._status_history)[-n_values:]
        else:
            statuses = [DetectionStatus.NORMAL] * n_values

        for idx in range(len(points) - 1):
            x1, y1 = points[idx]
            x2, y2 = points[idx + 1]
            seg_color = STATUS_COLORS.get(statuses[idx], color)
            canvas.create_line(x1, y1, x2, y2, fill=seg_color, width=2)

        if times:
            t_start = times[0]
            t_end = times[-1]
            for idx in range(5):
                t_val = t_start + (t_end - t_start) * idx / 4
                x_pos = margin_l + (idx / 4) * plot_w
                canvas.create_text(
                    x_pos,
                    margin_t + plot_h + 12,
                    text=f"{t_val:.0f}s",
                    fill=COLOR_TEXT,
                    font=("Consolas", 7),
                )

    def _draw_na_chart(self, canvas: tk.Canvas, message: str) -> None:
        """當功能不可用時，顯示簡短提示。"""
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 10 or height < 10:
            return

        canvas.create_rectangle(
            0,
            0,
            width,
            height,
            fill="#1a1a2e",
            outline=COLOR_GRID,
        )
        canvas.create_text(
            width / 2,
            height / 2,
            text=message,
            fill=COLOR_TEXT,
            font=("Microsoft JhengHei UI", 12, "bold"),
            justify=tk.CENTER,
        )

    def _update_alert_text(self) -> None:
        """更新告警記錄面板。"""
        current_count = int(self._alert_text.index("end-1c").split(".")[0])
        if not self._alert_log:
            return

        new_start = max(0, current_count - 1)
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
                f"  STI={sti:.4f}  相似度={sim:.4f}  持續={dur:.1f}秒\n\n"
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
        self._stop_recording(
            reason="視窗關閉，已儲存目前錄製資料。",
            interrupted=False,
            save_if_samples=True,
        )
        if self._detection_thread and self._detection_thread.is_alive():
            self._detection_thread.join(timeout=2.0)
        self.root.destroy()


def main() -> None:
    args = parse_args()
    app = FallDetectionGUI(args)
    app.run()


if __name__ == "__main__":
    main()
