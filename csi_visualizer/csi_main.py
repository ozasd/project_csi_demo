"""
ESP32 CSI 3D visualizer entrypoint.

Usage:
    python csi_main.py --com COM3
    python csi_main.py --com COM3 --static
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import config as cfg
from src.app_env import (
    ESP32_DEFAULT_BAUD_ENV,
    ESP32_DEFAULT_COM_ENV,
    env_int,
    env_text,
    load_env_file,
)
from src.csi_3d_display import CSI3DDisplay
from src.esp32_csi_reader import ESP32CSISource
from src.motion_detector import MotionDetector
from src.wifi_scanner import WiFiScanner


def parse_args() -> argparse.Namespace:
    load_env_file()
    default_com = env_text(ESP32_DEFAULT_COM_ENV, "COM3")
    default_baud = env_int(ESP32_DEFAULT_BAUD_ENV, 921600)
    parser = argparse.ArgumentParser(
        description="ESP32 CSI 3D visualizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python csi_main.py --com COM3\n"
            "  python csi_main.py --com COM3 --static\n"
            "  python csi_main.py --com COM4 --port 8080"
        ),
    )
    parser.add_argument("--com", default=default_com, help=f"ESP32 serial port (default: {default_com})")
    parser.add_argument("--baud", type=int, default=default_baud, help=f"ESP32 serial baudrate (default: {default_baud})")
    parser.add_argument("--static", action="store_true", help="Write static HTML instead of Dash")
    parser.add_argument("--port", type=int, default=8050, help="Dash server port")
    parser.add_argument("--frames", type=int, default=80, help="Number of frames to retain in the time axis")
    parser.add_argument("--refresh", type=int, default=250, help="Display refresh interval in milliseconds")
    parser.add_argument(
        "--log-file",
        help="CSV path for dashboard refresh logs (default: data/csi_runtime_log_YYYYMMDD_HHMMSS.csv)",
    )
    parser.add_argument(
        "--baseline-frames",
        type=int,
        default=cfg.SCENE_BASELINE_FRAMES,
        help=f"Frames used to initialize the static scene baseline (default: {cfg.SCENE_BASELINE_FRAMES})",
    )
    parser.add_argument(
        "--baseline-timeout",
        type=int,
        default=cfg.SCENE_BASELINE_TIMEOUT,
        help=f"Seconds to wait for static scene initialization (default: {cfg.SCENE_BASELINE_TIMEOUT})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=cfg.MOTION_THRESHOLD_STD,
        help=f"RSSI std threshold (default: {cfg.MOTION_THRESHOLD_STD})",
    )
    parser.add_argument(
        "--composite-threshold",
        type=float,
        default=cfg.MOTION_THRESHOLD_COMPOSITE,
        help=f"Composite motion threshold (default: {cfg.MOTION_THRESHOLD_COMPOSITE})",
    )
    return parser.parse_args()


def print_banner(args: argparse.Namespace) -> None:
    mode = "Static HTML" if args.static else "Dash live"
    print(
        "\n"
        "========================================\n"
        "  ESP32 CSI 3D Visualizer\n"
        "========================================\n"
        f"  Serial : {args.com} @ {args.baud}\n"
        f"  Output : {mode}\n"
        f"  Frames : {args.frames}\n"
        f"  Scene  : {args.baseline_frames} baseline frames\n"
        f"  Refresh: {args.refresh} ms\n"
        f"  Log    : {args.log_file or 'data/csi_runtime_log_YYYYMMDD_HHMMSS.csv'}\n"
    )


def feed_loop(scanner: WiFiScanner, detector: MotionDetector) -> None:
    last_index = 0
    while getattr(scanner, "_running", False):
        samples = scanner.get_latest_samples(200)
        for sample in samples[last_index:]:
            detector.feed(sample)
        last_index = len(samples)
        time.sleep(0.05)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )
    print_banner(args)

    print("[INIT] Starting Wi-Fi scanner...")
    scanner = WiFiScanner()

    print("[INIT] Starting motion detector...")
    detector = MotionDetector(
        threshold_std=args.threshold,
        threshold_composite=args.composite_threshold,
    )

    print(f"[CSI] Opening ESP32 CSI on {args.com} @ {args.baud}...")
    csi_source = ESP32CSISource(
        port=args.com,
        baudrate=args.baud,
        baseline_frames=args.baseline_frames,
        smoothing_window=cfg.SCENE_SMOOTHING_WINDOW,
        scene_window=cfg.SCENE_AVERAGE_WINDOW,
        contour_sigma=cfg.SCENE_CONTOUR_SIGMA,
        contour_floor=cfg.SCENE_CONTOUR_FLOOR,
    )

    try:
        csi_source.start()
    except Exception as exc:
        print(f"[ERR] Failed to open ESP32 CSI: {exc}")
        print("[TIP] Confirm the board is on the selected COM port and the ESP-CSI firmware is running.")
        print("[TIP] See docs/esp32_csi_flash_guide.md")
        return 1

    print("[CSI] Waiting for CSI frames...")
    deadline = time.time() + 30
    while time.time() < deadline:
        if csi_source.get_latest_frame() is not None:
            break
        time.sleep(0.5)
    else:
        print(f"[ERR] No CSI frame received. Stats: {csi_source.stats}")
        print("[TIP] Run idf.py monitor and make sure the ESP32 is printing CSI_DATA lines first.")
        csi_source.stop()
        return 1

    print(f"[OK] ESP32 CSI stream is alive. Stats: {csi_source.stats}")

    print(
        f"[SCENE] Initializing static scene baseline with {args.baseline_frames} CSI frames..."
    )
    print("[TIP] Keep the monitored space still during this step.")
    baseline_deadline = time.time() + args.baseline_timeout
    last_report = -1
    while time.time() < baseline_deadline:
        snapshot = csi_source.get_scene_snapshot()
        if snapshot.is_calibrated:
            print(
                "[OK] Static scene baseline locked. "
                f"Foreground ratio={snapshot.foreground_ratio:.0%}, "
                f"motion energy={snapshot.motion_energy:.3f}"
            )
            break

        progress_percent = int(snapshot.calibration_progress * 100)
        if progress_percent != last_report:
            print(f"[SCENE] Progress {progress_percent}%")
            last_report = progress_percent
        time.sleep(0.25)
    else:
        snapshot = csi_source.get_scene_snapshot()
        print(
            "[WARN] Static scene baseline did not finish before timeout. "
            f"Current progress={snapshot.calibration_progress:.0%}. "
            "The UI will continue updating and complete calibration in the background."
        )

    scanner.start(poll_interval=cfg.POLL_INTERVAL, scan_interval=cfg.SCAN_INTERVAL)
    threading.Thread(target=feed_loop, args=(scanner, detector), daemon=True).start()

    display = CSI3DDisplay(
        scanner=scanner,
        detector=detector,
        csi_source=csi_source,
        update_interval_ms=args.refresh,
        max_time_frames=args.frames,
        log_path=args.log_file,
    )
    print(f"[LOG] Saving refresh log to {display.log_path}")

    try:
        if args.static:
            display.run_static()
        else:
            display.run_dash(port=args.port)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[STOP] Shutting down...")
        csi_source.stop()
        scanner.stop()
        print("[DONE] Exit complete.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
