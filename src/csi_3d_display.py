"""
CSI 三圖視覺化介面。

版面配置：
1. 左側：3D 差分雲霧圖
2. 右上：子載波變化圖
3. 右下：STFT Spectrogram

說明：
- 右下採用真正的 STFT 頻譜圖，X 軸為時間，Y 軸為頻率。
- 為避免每次更新時畫面跳動，會固定 figure 高度，並對顯示尺度做緩慢更新。
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from dataclasses import dataclass

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.esp32_csi_reader import CSISource, CSIFrame, SceneSnapshot
from src.motion_detector import MotionDetector, MotionState
from src.wifi_scanner import WiFiScanner


SIGNED_SUBCARRIERS = np.array(list(range(-26, 0)) + list(range(1, 27)), dtype=np.int32)
SUBCARRIER_TICKS = [-26, -20, -15, -10, -5, -1, 1, 5, 10, 15, 20, 26]

DEFAULT_FIGURE_HEIGHT = 920
DEFAULT_CLOUD_Z_MAX = 1.10
DEFAULT_PROFILE_Y_MAX = 1.10
DEFAULT_DELTA_Y_MAX = 0.28
DEFAULT_SPECTROGRAM_Z_MAX = 0.32

BASE_COLORSCALE = [
    [0.0, "#0d1b2a"],
    [0.2, "#1b263b"],
    [0.4, "#415a77"],
    [0.6, "#778da9"],
    [0.8, "#a9d6e5"],
    [1.0, "#e0fbfc"],
]

DELTA_COLORSCALE = [
    [0.0, "#081c15"],
    [0.15, "#1b4332"],
    [0.35, "#2d6a4f"],
    [0.55, "#40916c"],
    [0.75, "#74c69d"],
    [0.9, "#f4d35e"],
    [1.0, "#ee964b"],
]

SPECTROGRAM_COLORSCALE = [
    [0.0, "#132b9c"],
    [0.15, "#1464f4"],
    [0.35, "#00b4ff"],
    [0.55, "#63e6be"],
    [0.75, "#d9f99d"],
    [0.9, "#ffd43b"],
    [1.0, "#ff4d4f"],
]


@dataclass
class DisplayScales:
    """儲存顯示尺度，避免每次更新大幅跳動。"""

    figure_height: int = DEFAULT_FIGURE_HEIGHT
    cloud_z_max: float = DEFAULT_CLOUD_Z_MAX
    profile_y_max: float = DEFAULT_PROFILE_Y_MAX
    delta_y_max: float = DEFAULT_DELTA_Y_MAX
    spectrogram_z_max: float = DEFAULT_SPECTROGRAM_Z_MAX


def _smooth_scale(current: float, target: float, minimum: float) -> float:
    """讓尺度緩慢逼近目標值，避免 update 時畫面亂跳。"""
    target = max(target, minimum)
    if target > current:
        return current * 0.82 + target * 0.18
    return current * 0.96 + target * 0.04


def _build_status(snapshot: SceneSnapshot, detector: MotionDetector) -> tuple[str, str]:
    """依據基線與動態狀態決定標題文字。"""
    if not snapshot.is_calibrated:
        return f"靜態空間初始化中 {snapshot.calibration_progress:.0%}", "#ffd166"
    if detector.state == MotionState.MOTION:
        return "偵測到動態變化", "#ff6b6b"
    if snapshot.foreground_ratio >= 0.18 or snapshot.motion_energy >= 0.10:
        return "場景相對基線已有差異", "#f4a261"
    return "背景穩定", "#4cc9f0"


def _prepare_frame_arrays(frames: list[CSIFrame]) -> tuple[np.ndarray, np.ndarray]:
    """把 frames 轉成時間軸與振幅矩陣。"""
    base_time = frames[0].timestamp
    time_axis = np.array([frame.timestamp - base_time for frame in frames], dtype=np.float64)
    amplitude_matrix = np.array([frame.amplitudes for frame in frames], dtype=np.float64)
    return time_axis, amplitude_matrix


def _smooth_matrix(matrix: np.ndarray) -> np.ndarray:
    """對矩陣做簡單平滑，讓圖像更連續。"""
    if matrix.size == 0:
        return matrix

    time_kernel = np.array([0.2, 0.6, 0.2], dtype=np.float64)
    padded_time = np.pad(matrix, ((1, 1), (0, 0)), mode="edge")
    time_smoothed = (
        padded_time[:-2] * time_kernel[0]
        + padded_time[1:-1] * time_kernel[1]
        + padded_time[2:] * time_kernel[2]
    )

    sub_kernel = np.array([0.15, 0.7, 0.15], dtype=np.float64)
    padded_sub = np.pad(time_smoothed, ((0, 0), (1, 1)), mode="edge")
    sub_smoothed = (
        padded_sub[:, :-2] * sub_kernel[0]
        + padded_sub[:, 1:-1] * sub_kernel[1]
        + padded_sub[:, 2:] * sub_kernel[2]
    )
    return sub_smoothed


def _compute_display_matrix(
    amplitude_matrix: np.ndarray,
    snapshot: SceneSnapshot,
) -> tuple[np.ndarray, str, list[list[float]]]:
    """產生雲霧圖與顏色映射使用的矩陣。"""
    if snapshot.is_calibrated and snapshot.baseline_profile is not None:
        delta_matrix = np.abs(amplitude_matrix - snapshot.baseline_profile[np.newaxis, :])
        return _smooth_matrix(delta_matrix), "基線差分", DELTA_COLORSCALE
    return _smooth_matrix(amplitude_matrix), "振幅", BASE_COLORSCALE


def _build_motion_signal(
    amplitude_matrix: np.ndarray,
    snapshot: SceneSnapshot,
) -> np.ndarray:
    """
    把 CSI 矩陣壓成單一時間訊號，供 STFT 使用。

    若已有背景基線，優先看相對基線變化。
    若沒有基線，暫時看相對平均值的變化。
    """
    if snapshot.baseline_profile is not None:
        motion_matrix = amplitude_matrix - snapshot.baseline_profile[np.newaxis, :]
    else:
        motion_matrix = amplitude_matrix - np.mean(amplitude_matrix, axis=0, keepdims=True)

    if snapshot.silhouette_profile is not None:
        weights = np.abs(snapshot.silhouette_profile) + 0.25
    else:
        weights = np.ones(amplitude_matrix.shape[1], dtype=np.float64)

    signal = np.mean(motion_matrix * weights[np.newaxis, :], axis=1)
    signal = np.diff(signal, prepend=signal[0])
    signal = signal - np.mean(signal)
    return signal


def _compute_spectrogram(
    time_axis: np.ndarray,
    amplitude_matrix: np.ndarray,
    snapshot: SceneSnapshot,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """
    以單一時間訊號做 STFT 頻譜圖。

    回傳：
    - centers: 每個窗中心時間
    - freq_axis: 頻率軸
    - spectrogram: 頻譜能量矩陣，shape = [freq, time]
    """
    if len(time_axis) < 8:
        return None

    deltas = np.diff(time_axis)
    valid_deltas = deltas[deltas > 1e-4]
    if valid_deltas.size == 0:
        return None

    dt = float(np.median(valid_deltas))
    sample_rate = 1.0 / dt
    motion_signal = _build_motion_signal(amplitude_matrix, snapshot)

    if len(motion_signal) < 8:
        return None

    window_size = min(32, len(motion_signal))
    if window_size < 8:
        return None

    step = max(1, window_size // 4)
    window = np.hanning(window_size)
    spectra = []
    centers = []

    for start in range(0, len(motion_signal) - window_size + 1, step):
        segment = motion_signal[start : start + window_size] * window
        spectrum = np.abs(np.fft.rfft(segment))
        freq_axis = np.fft.rfftfreq(window_size, d=dt)

        # 略過 0 Hz 的 DC 能量，只保留低頻動態特徵。
        mask = (freq_axis > 0.05) & (freq_axis <= min(sample_rate / 2.0, 6.0))
        if not np.any(mask):
            continue

        spectra.append(spectrum[mask])
        centers.append(time_axis[start + window_size // 2])

    if not spectra:
        return None

    filtered_freq_axis = freq_axis[mask]
    spectrogram = np.array(spectra, dtype=np.float64).T
    spectrogram = np.log1p(spectrogram)
    return np.array(centers, dtype=np.float64), filtered_freq_axis, spectrogram


def _estimate_targets(
    amplitude_matrix: np.ndarray,
    display_matrix: np.ndarray,
    spectrogram: np.ndarray | None,
    snapshot: SceneSnapshot,
) -> tuple[float, float, float, float]:
    """估計當前畫面該往哪個尺度靠近。"""
    cloud_target = max(DEFAULT_CLOUD_Z_MAX, float(np.percentile(amplitude_matrix, 99) * 1.15))

    profile_sources = [amplitude_matrix]
    if snapshot.baseline_profile is not None:
        profile_sources.append(snapshot.baseline_profile[np.newaxis, :])
    profile_target = max(
        DEFAULT_PROFILE_Y_MAX,
        float(np.max([np.percentile(values, 99) for values in profile_sources]) * 1.12),
    )

    if snapshot.delta_profile is not None:
        delta_target = max(DEFAULT_DELTA_Y_MAX, float(np.percentile(snapshot.delta_profile, 98) * 1.25))
    else:
        delta_target = DEFAULT_DELTA_Y_MAX

    if spectrogram is not None:
        spectrogram_target = max(
            DEFAULT_SPECTROGRAM_Z_MAX,
            float(np.percentile(spectrogram, 99) * 1.10),
        )
    else:
        spectrogram_target = DEFAULT_SPECTROGRAM_Z_MAX

    return cloud_target, profile_target, delta_target, spectrogram_target


def _empty_figure(message: str, scales: DisplayScales) -> go.Figure:
    """建立資料不足時的佔位圖。"""
    fig = make_subplots(
        rows=2,
        cols=2,
        specs=[
            [{"type": "scene", "rowspan": 2}, {"type": "xy", "secondary_y": True}],
            [None, {"type": "heatmap"}],
        ],
        column_widths=[0.62, 0.38],
        row_heights=[0.46, 0.54],
        horizontal_spacing=0.06,
        vertical_spacing=0.10,
        subplot_titles=("3D 差分雲霧圖", "子載波變化圖", "STFT Spectrogram"),
    )
    fig.update_layout(
        paper_bgcolor="#08111f",
        plot_bgcolor="#08111f",
        font=dict(family="Microsoft JhengHei, sans-serif", color="#dbe7f3"),
        height=scales.figure_height,
        margin=dict(l=18, r=18, t=86, b=20),
        title=dict(text=message, x=0.5, xanchor="center"),
        uirevision="csi-layout-static",
    )
    fig.add_annotation(
        text="等待 CSI 幀資料...",
        x=0.20,
        y=0.54,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=18, color="#8aa0b7"),
    )
    fig.add_annotation(
        text="右上會顯示基線 / 目前 / 差分 / 輪廓代理",
        x=0.79,
        y=0.66,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=13, color="#6f859b"),
    )
    fig.add_annotation(
        text="右下會顯示 STFT 頻譜圖",
        x=0.79,
        y=0.24,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=13, color="#6f859b"),
    )
    return fig


def _add_cloud_panel(
    fig: go.Figure,
    time_axis: np.ndarray,
    amplitude_matrix: np.ndarray,
    display_matrix: np.ndarray,
    color_title: str,
    color_scale: list[list[float]],
    snapshot: SceneSnapshot,
    scales: DisplayScales,
) -> None:
    """加入左側 3D 差分雲霧圖。"""
    if snapshot.is_calibrated and snapshot.baseline_profile is not None:
        fig.add_trace(
            go.Surface(
                x=np.tile(SIGNED_SUBCARRIERS, (len(time_axis), 1)),
                y=np.tile(time_axis[:, np.newaxis], (1, len(SIGNED_SUBCARRIERS))),
                z=np.tile(snapshot.baseline_profile[np.newaxis, :], (len(time_axis), 1)),
                surfacecolor=np.zeros_like(amplitude_matrix),
                colorscale=[[0.0, "#355070"], [1.0, "#355070"]],
                opacity=0.18,
                showscale=False,
                hoverinfo="skip",
                name="靜止前基線",
            ),
            row=1,
            col=1,
        )

    color_values = display_matrix.reshape(-1)
    amplitude_values = amplitude_matrix.reshape(-1)
    color_norm = np.clip(color_values / max(scales.spectrogram_z_max, 1e-6), 0.0, 1.0)
    marker_sizes = 4 + color_norm * 11

    fig.add_trace(
        go.Scatter3d(
            x=np.tile(SIGNED_SUBCARRIERS, len(time_axis)),
            y=np.repeat(time_axis, len(SIGNED_SUBCARRIERS)),
            z=amplitude_values,
            mode="markers",
            name="CSI 雲霧",
            customdata=np.column_stack([color_values]),
            marker=dict(
                size=marker_sizes,
                color=color_values,
                colorscale=color_scale,
                cmin=0.0,
                cmax=scales.spectrogram_z_max,
                opacity=0.72,
                line=dict(width=0),
                colorbar=dict(
                    title=dict(text=color_title, font=dict(color="#dbe7f3", size=12)),
                    tickfont=dict(color="#8aa0b7", size=10),
                    bgcolor="rgba(8,17,31,0.84)",
                    bordercolor="#203247",
                    borderwidth=1,
                    len=0.60,
                    x=0.59,
                ),
            ),
            hovertemplate=(
                "子載波 %{x}<br>"
                "時間 %{y:.2f}s<br>"
                "振幅 %{z:.3f}<br>"
                "強度 %{customdata[0]:.3f}<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )


def _add_profile_panel(fig: go.Figure, snapshot: SceneSnapshot) -> None:
    """加入右上子載波變化圖。"""
    x_axis = SIGNED_SUBCARRIERS

    if snapshot.baseline_profile is not None:
        fig.add_trace(
            go.Scatter(
                x=x_axis,
                y=snapshot.baseline_profile,
                mode="lines",
                name="靜止前基線",
                line=dict(color="#4cc9f0", width=2),
            ),
            row=1,
            col=2,
            secondary_y=False,
        )

    if snapshot.current_profile is not None:
        fig.add_trace(
            go.Scatter(
                x=x_axis,
                y=snapshot.current_profile,
                mode="lines",
                name="目前場景",
                line=dict(color="#ffffff", width=2.5),
            ),
            row=1,
            col=2,
            secondary_y=False,
        )

    if snapshot.delta_profile is not None:
        fig.add_trace(
            go.Scatter(
                x=x_axis,
                y=snapshot.delta_profile,
                mode="lines",
                name="前景差分",
                line=dict(color="#ee964b", width=2),
                fill="tozeroy",
                fillcolor="rgba(238, 150, 75, 0.18)",
            ),
            row=1,
            col=2,
            secondary_y=True,
        )

    if snapshot.silhouette_profile is not None:
        fig.add_trace(
            go.Scatter(
                x=x_axis,
                y=snapshot.silhouette_profile,
                mode="lines",
                name="輪廓代理",
                line=dict(color="#ffd166", width=3, dash="dot"),
            ),
            row=1,
            col=2,
            secondary_y=True,
        )


def _add_spectrogram_panel(
    fig: go.Figure,
    spectrogram_result: tuple[np.ndarray, np.ndarray, np.ndarray] | None,
    scales: DisplayScales,
) -> None:
    """加入右下 STFT Spectrogram。"""
    if spectrogram_result is None:
        fig.add_annotation(
            text="需要更多連續 CSI 幀才能估算 STFT Spectrogram",
            x=0.79,
            y=0.18,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(size=12, color="#6f859b"),
        )
        return

    centers, freq_axis, spectrogram = spectrogram_result
    fig.add_trace(
        go.Heatmap(
            x=centers,
            y=freq_axis,
            z=spectrogram,
            colorscale=SPECTROGRAM_COLORSCALE,
            zmin=0.0,
            zmax=scales.spectrogram_z_max,
            zsmooth="best",
            colorbar=dict(
                title=dict(text="頻譜能量", font=dict(color="#dbe7f3", size=12)),
                tickfont=dict(color="#8aa0b7", size=10),
                bgcolor="rgba(8,17,31,0.84)",
                bordercolor="#203247",
                borderwidth=1,
                len=0.28,
                x=1.01,
                y=0.18,
            ),
            hovertemplate=(
                "時間 %{x:.2f}s<br>"
                "頻率 %{y:.2f} Hz<br>"
                "能量 %{z:.3f}<extra></extra>"
            ),
            name="STFT Spectrogram",
        ),
        row=2,
        col=2,
    )


def build_figure(
    csi_source: CSISource,
    detector: MotionDetector,
    max_time_frames: int,
    scales: DisplayScales,
) -> go.Figure:
    """建立三圖版 CSI 視覺化。"""
    frames = csi_source.get_latest_frames(max_time_frames)
    snapshot = csi_source.get_scene_snapshot()

    if len(frames) < 2:
        return _empty_figure("ESP32 CSI 空間差分視覺化", scales)

    time_axis, amplitude_matrix = _prepare_frame_arrays(frames)
    display_matrix, color_title, color_scale = _compute_display_matrix(amplitude_matrix, snapshot)
    spectrogram_result = _compute_spectrogram(time_axis, amplitude_matrix, snapshot)
    status_text, status_color = _build_status(snapshot, detector)

    fig = make_subplots(
        rows=2,
        cols=2,
        specs=[
            [{"type": "scene", "rowspan": 2}, {"type": "xy", "secondary_y": True}],
            [None, {"type": "heatmap"}],
        ],
        column_widths=[0.62, 0.38],
        row_heights=[0.46, 0.54],
        horizontal_spacing=0.06,
        vertical_spacing=0.10,
        subplot_titles=("3D 差分雲霧圖", "子載波變化圖", "STFT Spectrogram"),
    )

    _add_cloud_panel(
        fig,
        time_axis,
        amplitude_matrix,
        display_matrix,
        color_title,
        color_scale,
        snapshot,
        scales,
    )
    _add_profile_panel(fig, snapshot)
    _add_spectrogram_panel(fig, spectrogram_result, scales)

    latest_frame = frames[-1]
    metric_text = (
        f"RSSI {latest_frame.rssi_dbm:.0f} dBm | "
        f"差分均值 {snapshot.motion_energy:.3f} | "
        f"輪廓覆蓋 {snapshot.foreground_ratio:.0%} | "
        f"最大差分 {snapshot.peak_delta:.3f}"
    )

    max_time = max(2.0, float(time_axis.max()))
    fig.update_layout(
        paper_bgcolor="#08111f",
        plot_bgcolor="#08111f",
        font=dict(family="Microsoft JhengHei, sans-serif", color="#dbe7f3"),
        height=scales.figure_height,
        margin=dict(l=18, r=18, t=90, b=20),
        title=dict(
            text=(
                "<b>ESP32 CSI 空間差分視覺化</b>"
                f"<br><span style='color:{status_color};font-size:15px'>{status_text}</span>"
            ),
            x=0.5,
            xanchor="center",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(8,17,31,0.72)",
            bordercolor="#203247",
            borderwidth=1,
        ),
        uirevision="csi-layout-static",
    )
    fig.add_annotation(
        text=metric_text,
        showarrow=False,
        x=0.5,
        y=-0.03,
        xref="paper",
        yref="paper",
        xanchor="center",
        font=dict(size=11, color="#90a7bd"),
    )

    fig.update_scenes(
        xaxis=dict(
            title="Signed 子載波",
            backgroundcolor="#0b1626",
            gridcolor="#203247",
            showbackground=True,
            tickmode="array",
            tickvals=SUBCARRIER_TICKS,
            range=[-27, 27],
        ),
        yaxis=dict(
            title="時間 (秒)",
            backgroundcolor="#0b1626",
            gridcolor="#203247",
            showbackground=True,
            range=[0.0, max_time],
        ),
        zaxis=dict(
            title="振幅",
            backgroundcolor="#0b1626",
            gridcolor="#203247",
            showbackground=True,
            range=[0, scales.cloud_z_max],
        ),
        camera=dict(eye=dict(x=1.55, y=-1.85, z=0.85)),
        aspectratio=dict(x=1.35, y=2.0, z=0.8),
        uirevision="csi-scene-static",
    )

    fig.update_xaxes(
        title_text="Signed 子載波",
        tickmode="array",
        tickvals=SUBCARRIER_TICKS,
        gridcolor="#203247",
        zerolinecolor="#203247",
        row=1,
        col=2,
    )
    fig.update_yaxes(
        title_text="振幅",
        gridcolor="#203247",
        zerolinecolor="#203247",
        range=[0, scales.profile_y_max],
        row=1,
        col=2,
        secondary_y=False,
    )
    fig.update_yaxes(
        title_text="差分 / 輪廓",
        gridcolor="#203247",
        zerolinecolor="#203247",
        range=[0, scales.delta_y_max],
        row=1,
        col=2,
        secondary_y=True,
    )
    fig.update_xaxes(
        title_text="時間 (秒)",
        gridcolor="#203247",
        zerolinecolor="#203247",
        range=[0.0, max_time],
        row=2,
        col=2,
    )
    fig.update_yaxes(
        title_text="頻率 (Hz)",
        gridcolor="#203247",
        zerolinecolor="#203247",
        row=2,
        col=2,
    )

    return fig


class CSI3DDisplay:
    """CSI 視覺化控制器，支援 Dash 即時模式與靜態 HTML 模式。"""

    def __init__(
        self,
        scanner: WiFiScanner,
        detector: MotionDetector,
        csi_source: CSISource,
        update_interval_ms: int = 1500,
        max_time_frames: int = 80,
    ):
        self.scanner = scanner
        self.detector = detector
        self.csi_source = csi_source
        self.update_interval_ms = update_interval_ms
        self.max_time_frames = max_time_frames
        self._scales = DisplayScales()

    def _refresh_scales(self) -> None:
        """根據目前資料更新顯示尺度，但以緩慢方式更新。"""
        frames = self.csi_source.get_latest_frames(self.max_time_frames)
        snapshot = self.csi_source.get_scene_snapshot()
        if len(frames) < 2:
            return

        time_axis, amplitude_matrix = _prepare_frame_arrays(frames)
        display_matrix, _, _ = _compute_display_matrix(amplitude_matrix, snapshot)
        spectrogram_result = _compute_spectrogram(time_axis, amplitude_matrix, snapshot)
        spectrogram = spectrogram_result[2] if spectrogram_result is not None else None

        cloud_target, profile_target, delta_target, spectrogram_target = _estimate_targets(
            amplitude_matrix,
            display_matrix,
            spectrogram,
            snapshot,
        )

        self._scales.cloud_z_max = _smooth_scale(
            self._scales.cloud_z_max,
            cloud_target,
            DEFAULT_CLOUD_Z_MAX,
        )
        self._scales.profile_y_max = _smooth_scale(
            self._scales.profile_y_max,
            profile_target,
            DEFAULT_PROFILE_Y_MAX,
        )
        self._scales.delta_y_max = _smooth_scale(
            self._scales.delta_y_max,
            delta_target,
            DEFAULT_DELTA_Y_MAX,
        )
        self._scales.spectrogram_z_max = _smooth_scale(
            self._scales.spectrogram_z_max,
            spectrogram_target,
            DEFAULT_SPECTROGRAM_Z_MAX,
        )

    def _build_current_figure(self) -> go.Figure:
        """更新尺度後建立目前畫面。"""
        self._refresh_scales()
        return build_figure(
            self.csi_source,
            self.detector,
            self.max_time_frames,
            self._scales,
        )

    def run_dash(self, port: int = 8050, debug: bool = False) -> None:
        """啟動 Dash 即時頁面。"""
        try:
            from dash import Dash, dcc, html
            from dash.dependencies import Input, Output
        except ImportError:
            print("[WARN] 未安裝 Dash，改用靜態 HTML 模式。")
            self.run_static()
            return

        app = Dash(__name__, title="ESP32 CSI 三圖視覺化")
        app.index_string = """<!DOCTYPE html>
<html lang="zh-Hant">
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            html, body {
                margin: 0;
                padding: 0;
                width: 100%;
                height: 100%;
                background: #08111f;
                color: #dbe7f3;
            }
            * {
                box-sizing: border-box;
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

        app.layout = html.Div(
            style={
                "backgroundColor": "#08111f",
                "margin": "0",
                "padding": "0",
                "minHeight": "100vh",
                "display": "flex",
                "flexDirection": "column",
                "fontFamily": "Microsoft JhengHei, sans-serif",
            },
            children=[
                html.Div(
                    style={
                        "background": "linear-gradient(135deg, #08111f 0%, #132238 100%)",
                        "padding": "12px 24px",
                        "borderBottom": "1px solid #203247",
                        "display": "flex",
                        "justifyContent": "space-between",
                        "alignItems": "center",
                    },
                    children=[
                        html.Div(
                            "ESP32 CSI 三圖視覺化",
                            style={
                                "fontSize": "18px",
                                "fontWeight": "700",
                                "color": "#dbe7f3",
                            },
                        ),
                        html.Div(id="status-text", style={"fontSize": "13px", "color": "#90a7bd"}),
                    ],
                ),
                dcc.Graph(
                    id="csi-figure",
                    style={
                        "height": "calc(100vh - 96px)",
                        "width": "100%",
                        "margin": "0",
                        "padding": "0",
                    },
                    config={"displaylogo": False, "responsive": True},
                ),
                html.Div(
                    id="update-info",
                    style={
                        "padding": "8px 24px",
                        "borderTop": "1px solid #203247",
                        "fontSize": "11px",
                        "color": "#90a7bd",
                        "backgroundColor": "#0b1626",
                    },
                ),
                dcc.Interval(
                    id="refresh-interval",
                    interval=self.update_interval_ms,
                    n_intervals=0,
                ),
            ],
        )

        @app.callback(
            Output("csi-figure", "figure"),
            Output("status-text", "children"),
            Output("update-info", "children"),
            Input("refresh-interval", "n_intervals"),
        )
        def update_graph(n_intervals: int):
            snapshot = self.csi_source.get_scene_snapshot()
            figure = self._build_current_figure()

            if snapshot.is_calibrated:
                status = (
                    f"基線已鎖定 | 差分均值 {snapshot.motion_energy:.3f} | "
                    f"輪廓覆蓋 {snapshot.foreground_ratio:.0%}"
                )
            else:
                status = f"初始化靜態空間中 {snapshot.calibration_progress:.0%}"

            update_info = (
                f"刷新次數 {n_intervals} | 更新間隔 {self.update_interval_ms} ms | "
                f"固定高度 {self._scales.figure_height}px"
            )
            return figure, status, update_info

        print(f"[WEB] 開啟 Dash 視覺化：http://localhost:{port}")
        print("[TIP] 右下已改成真正的 STFT Spectrogram，並固定 body margin 與顯示高度。")
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
        app.run(host="0.0.0.0", port=port, debug=debug)

    def run_static(self) -> None:
        """定期輸出靜態 HTML。"""
        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "csi_scene_delta.html",
        )
        os.makedirs(os.path.dirname(html_path), exist_ok=True)

        print(f"[FILE] 靜態輸出：{html_path}")
        print("[TIP] 右下已改成真正的 STFT Spectrogram，並固定 body margin 與顯示高度。")

        first_open = True
        try:
            while True:
                figure = self._build_current_figure()
                html_content = self._build_full_html(figure)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write(html_content)

                if first_open:
                    webbrowser.open(f"file:///{html_path}")
                    first_open = False

                snapshot = self.csi_source.get_scene_snapshot()
                if snapshot.is_calibrated:
                    status = (
                        f"基線已鎖定 | 差分均值 {snapshot.motion_energy:.3f} | "
                        f"輪廓覆蓋 {snapshot.foreground_ratio:.0%}"
                    )
                else:
                    status = f"初始化中 {snapshot.calibration_progress:.0%}"

                print(
                    f"\r[CSI] {status} | 幀數 {len(self.csi_source.get_latest_frames())}",
                    end="",
                    flush=True,
                )
                time.sleep(self.update_interval_ms / 1000.0)
        except KeyboardInterrupt:
            print("\n[STOP] 靜態模式結束。")

    def _build_full_html(self, figure: go.Figure) -> str:
        """包裝完整靜態 HTML。"""
        figure_html = figure.to_html(
            full_html=False,
            include_plotlyjs="cdn",
            config={"displaylogo": False, "responsive": True},
        )
        refresh_seconds = max(2, self.update_interval_ms // 1000)

        return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="{refresh_seconds}">
    <title>ESP32 CSI 三圖視覺化</title>
    <style>
        html, body {{
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            background: #08111f;
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            color: #dbe7f3;
            font-family: "Microsoft JhengHei", sans-serif;
            display: flex;
            flex-direction: column;
        }}
        .header {{
            padding: 14px 24px;
            background: linear-gradient(135deg, #08111f 0%, #132238 100%);
            border-bottom: 1px solid #203247;
        }}
        .header h1 {{
            margin: 0;
            font-size: 20px;
        }}
        .header p {{
            margin: 6px 0 0 0;
            color: #90a7bd;
            font-size: 12px;
        }}
        .content {{
            flex: 1;
            min-height: 0;
            width: 100%;
        }}
        .footer {{
            padding: 10px 24px;
            border-top: 1px solid #203247;
            color: #90a7bd;
            font-size: 11px;
            background: #0b1626;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>ESP32 CSI 三圖視覺化</h1>
        <p>左：3D 差分雲霧圖｜右上：子載波變化圖｜右下：STFT Spectrogram｜每 {refresh_seconds} 秒自動刷新</p>
    </div>
    <div class="content">{figure_html}</div>
    <div class="footer">
        HTML body margin 已設為 0，並固定 figure 高度。右下為真正的 STFT 頻譜圖。
    </div>
</body>
</html>"""
