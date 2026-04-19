"""
One-command launcher for the ESP32 CSI workflow.

Flow:
1. Open the ESP32 serial port
2. Reboot the board
3. Prompt for Wi-Fi credentials if the firmware asks for them
4. Wait until CSI_DATA appears
5. Replace this process with csi_main.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def maybe_reexec_into_wifi_env() -> None:
    current = os.path.normcase(os.path.abspath(sys.executable))
    candidates = [
        os.environ.get("WIFI_CSI_PYTHON"),
        os.path.expanduser(r"~\.conda\envs\wifi-csi\python.exe"),
        r"C:\ProgramData\anaconda3\envs\wifi-csi\python.exe",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        candidate = os.path.abspath(candidate)
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(candidate) == current:
            return

        print(f"[INIT] Switching to wifi-csi environment: {candidate}")
        os.execv(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ESP32 CSI launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py\n"
            "  python main.py --com COM4\n"
            "  python main.py --ssid \"Tracy 2\" --password a7802568\n"
            "  python main.py --static"
        ),
    )
    parser.add_argument("--com", default="COM3", help="ESP32 serial port")
    parser.add_argument("--baud", type=int, default=921600, help="ESP32 serial baudrate")
    parser.add_argument("--ssid", help="Wi-Fi SSID for non-interactive startup")
    parser.add_argument("--password", help="Wi-Fi password for non-interactive startup")
    parser.add_argument("--setup-timeout", type=int, default=20, help="Seconds to wait for prompt or CSI")
    parser.add_argument("--connect-timeout", type=int, default=30, help="Seconds to wait for CSI after sending Wi-Fi")
    parser.add_argument("--static", action="store_true", help="Launch csi_main.py in static HTML mode")
    parser.add_argument("--port", type=int, default=8050, help="Dash server port")
    parser.add_argument("--frames", type=int, default=80, help="Frame history size")
    parser.add_argument("--refresh", type=int, default=1000, help="Refresh interval in milliseconds")
    parser.add_argument("--baseline-frames", type=int, default=80, help="Static scene baseline frame count")
    parser.add_argument("--baseline-timeout", type=int, default=30, help="Static scene baseline timeout in seconds")
    parser.add_argument("--skip-setup", action="store_true", help="Skip Wi-Fi setup and go straight to csi_main.py")
    return parser.parse_args()


def format_wifi_line(ssid: str, password: str) -> str:
    if any(ch.isspace() for ch in ssid):
        ssid = f'"{ssid}"'
    return f"{ssid} {password}\n" if password else f"{ssid}\n"


def prompt_credentials(args: argparse.Namespace) -> tuple[str, str]:
    ssid = args.ssid.strip() if args.ssid else input("Wi-Fi SSID: ").strip()
    password = args.password if args.password is not None else input("Wi-Fi password (blank for open network): ")
    if not ssid:
        raise ValueError("Wi-Fi SSID cannot be empty.")
    return ssid, password


def reset_esp32(ser) -> None:
    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(0.2)
    ser.reset_input_buffer()
    ser.setDTR(True)
    ser.setRTS(True)


def read_until_prompt_or_csi(ser, timeout: int) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        print(f"[ESP32] {line}")
        if "Please input ssid password:" in line:
            return "prompt"
        if line.startswith("CSI_DATA"):
            return "csi"
    return "timeout"


def wait_for_csi(ser, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        print(f"[ESP32] {line}")
        if line.startswith("CSI_DATA"):
            return True
    return False


def launch_visualizer(args: argparse.Namespace) -> "NoReturn":
    csi_main_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "csi_main.py")
    argv = [
        sys.executable,
        csi_main_path,
        "--com",
        args.com,
        "--baud",
        str(args.baud),
        "--port",
        str(args.port),
        "--frames",
        str(args.frames),
        "--refresh",
        str(args.refresh),
        "--baseline-frames",
        str(args.baseline_frames),
        "--baseline-timeout",
        str(args.baseline_timeout),
    ]
    if args.static:
        argv.append("--static")
    print("[RUN] Launching csi_main.py ...")
    os.execv(sys.executable, argv)


def main() -> int:
    maybe_reexec_into_wifi_env()
    args = parse_args()

    if args.skip_setup:
        launch_visualizer(args)

    try:
        import serial
    except ImportError:
        print("[ERR] pyserial is required. Activate wifi-csi and install dependencies first.")
        return 1

    print(f"[INIT] Opening {args.com} @ {args.baud} ...")
    try:
        ser = serial.Serial(args.com, args.baud, timeout=0.5)
    except serial.SerialException as exc:
        print(f"[ERR] Cannot open {args.com}: {exc}")
        print("[TIP] Confirm the board is connected and no other program is using the port.")
        return 1

    try:
        print("[INIT] Rebooting ESP32 ...")
        reset_esp32(ser)

        state = read_until_prompt_or_csi(ser, args.setup_timeout)

        if state == "prompt":
            ssid, password = prompt_credentials(args)
            print("[SEND] Sending Wi-Fi credentials ...")
            ser.write(format_wifi_line(ssid, password).encode("utf-8"))
            ser.flush()
            if not wait_for_csi(ser, args.connect_timeout):
                print("[ERR] Timed out waiting for CSI_DATA after sending Wi-Fi credentials.")
                print("[TIP] Check SSID/password and confirm the ESP32 can reach that AP.")
                return 1
        elif state == "csi":
            print("[OK] CSI_DATA already active.")
        else:
            print("[ERR] Did not receive a Wi-Fi prompt or CSI_DATA from the board.")
            print("[TIP] Press EN on the ESP32 once and retry.")
            return 1
    finally:
        ser.close()

    launch_visualizer(args)


if __name__ == "__main__":
    raise SystemExit(main())
