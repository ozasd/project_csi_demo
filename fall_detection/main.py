"""
ESP32 CSI 跌倒偵測系統 — 一鍵啟動器。

流程：
1. 讀取 .env 取得 Wi-Fi SSID / 密碼
2. 開啟 ESP32 序列埠
3. 重啟 ESP32
4. 等待 Wi-Fi 提示或 CSI_DATA
5. 傳送 Wi-Fi 帳密
6. 啟動跌倒偵測 GUI
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from src.app_env import (
    ESP32_DEFAULT_BAUD_ENV,
    ESP32_DEFAULT_COM_ENV,
    ESP32_WIFI_LINE_ENDING_ENV,
    env_int,
    env_text,
    load_env_file,
)
from src.serial_utils import open_serial_for_setup, release_serial_control_lines

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

WIFI_DEFAULT_SSID_ENV = "WIFI_DEFAULT_SSID"
WIFI_DEFAULT_PASSWORD_ENV = "WIFI_DEFAULT_PASSWORD"
LINE_ENDINGS = {
    "cr": "\r",
    "lf": "\n",
    "crlf": "\r\n",
}


def maybe_reexec_into_wifi_env() -> None:
    """若目前不在 wifi-csi conda 環境，自動切換。"""
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

        print(f"[初始化] 切換至 wifi-csi 環境: {candidate}")
        os.execv(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]])


def parse_args() -> argparse.Namespace:
    default_com = env_text(ESP32_DEFAULT_COM_ENV, "COM3")
    default_baud = env_int(ESP32_DEFAULT_BAUD_ENV, 921600)
    parser = argparse.ArgumentParser(
        description="ESP32 CSI 跌倒偵測系統啟動器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "範例:\n"
            "  python main.py\n"
            "  python main.py --com COM4\n"
            '  python main.py --ssid "Tracy 2" --password a7802568\n'
            "  python main.py --simulate\n\n"
            "環境變數:\n"
            "  .env -> WIFI_DEFAULT_SSID / WIFI_DEFAULT_PASSWORD / ESP32_DEFAULT_COM / ESP32_DEFAULT_BAUD"
        ),
    )
    parser.add_argument("--com", default=default_com, help=f"ESP32 序列埠 (預設: {default_com})")
    parser.add_argument("--baud", type=int, default=default_baud, help=f"ESP32 鮑率 (預設: {default_baud})")
    parser.add_argument(
        "--ssid",
        help=f"Wi-Fi SSID（覆蓋 {WIFI_DEFAULT_SSID_ENV}）",
    )
    parser.add_argument(
        "--password",
        help=f"Wi-Fi 密碼（覆蓋 {WIFI_DEFAULT_PASSWORD_ENV}）",
    )
    parser.add_argument("--setup-timeout", type=int, default=20, help="等待 ESP32 提示的逾時秒數")
    parser.add_argument("--connect-timeout", type=int, default=30, help="等待 CSI_DATA 的逾時秒數")
    parser.add_argument(
        "--wifi-line-ending",
        choices=sorted(LINE_ENDINGS),
        default=env_text(ESP32_WIFI_LINE_ENDING_ENV, "cr").lower(),
        help="傳送 Wi-Fi 帳密時使用的行尾 (預設: cr)",
    )
    parser.add_argument("--skip-setup", action="store_true", help="跳過 Wi-Fi 設定，直接啟動 GUI")
    parser.add_argument("--simulate", action="store_true", help="使用模擬資料（不需要 ESP32）")
    # 偵測參數
    parser.add_argument("--sti-threshold", type=float, default=0.22, help="STI 門檻 (預設: 0.22)")
    parser.add_argument("--sim-threshold", type=float, default=0.65, help="相似度門檻 (預設: 0.65)")
    parser.add_argument("--duration-threshold", type=float, default=3.0, help="持續時間門檻 (預設: 3.0 秒)")
    return parser.parse_args()


def format_wifi_line(ssid: str, password: str, line_ending: str = "\r") -> str:
    if any(ch.isspace() for ch in ssid):
        ssid = f'"{ssid}"'
    return f"{ssid} {password}{line_ending}" if password else f"{ssid}{line_ending}"


def prompt_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """取得 Wi-Fi 帳密：優先使用命令列參數 → .env → 互動輸入。"""
    env_ssid = os.environ.get(WIFI_DEFAULT_SSID_ENV)
    env_password = os.environ.get(WIFI_DEFAULT_PASSWORD_ENV)

    if args.ssid:
        ssid = args.ssid.strip()
    elif env_ssid and env_ssid.strip():
        ssid = env_ssid.strip()
        print(f"[初始化] 使用 {WIFI_DEFAULT_SSID_ENV} 的預設 SSID。")
    else:
        ssid = input("Wi-Fi SSID: ").strip()

    if args.password is not None:
        password = args.password
    elif env_password is not None:
        password = env_password
        print(f"[初始化] 使用 {WIFI_DEFAULT_PASSWORD_ENV} 的預設密碼。")
    else:
        password = input("Wi-Fi 密碼（開放網路留空）: ")

    if not ssid:
        raise ValueError("Wi-Fi SSID 不得為空。")
    return ssid, password


def reset_esp32(ser) -> None:
    """釋放 ESP32 自動 reset 控制線，讓新 ESP32-S3 板子留在 app 模式。"""
    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(0.2)
    ser.reset_input_buffer()


def read_until_prompt_or_csi(ser, timeout: int) -> str:
    """等待 ESP32 輸出 Wi-Fi 提示或 CSI_DATA。"""
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
    """等待 CSI_DATA 出現。"""
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


def print_wifi_timeout_hints(ssid: str) -> None:
    print(f"[提示] 本次送出的 SSID: {ssid}")
    print("[提示] ESP32 只支援 2.4 GHz Wi-Fi，手機熱點請改用 2.4 GHz / WPA2-Personal。")
    print("[提示] 若手機熱點顯示 5 GHz、6 GHz 或 WPA3-only，ESP32 會連不上。")


def launch_gui(args: argparse.Namespace) -> None:
    """啟動跌倒偵測 GUI。"""
    gui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui.py")
    argv = [
        sys.executable,
        gui_path,
        "--com", args.com,
        "--baud", str(args.baud),
        "--sti-threshold", str(args.sti_threshold),
        "--sim-threshold", str(args.sim_threshold),
        "--duration-threshold", str(args.duration_threshold),
    ]
    if args.simulate:
        argv.append("--simulate")
    print("[啟動] 開啟跌倒偵測 GUI ...")
    os.execv(sys.executable, argv)


def main() -> int:
    maybe_reexec_into_wifi_env()
    load_env_file()
    args = parse_args()

    # 模擬模式或跳過設定 → 直接啟動 GUI
    if args.simulate or args.skip_setup:
        launch_gui(args)
        return 0  # unreachable after execv

    try:
        import serial
    except ImportError:
        print("[錯誤] 需要 pyserial。請先啟動 wifi-csi 環境並安裝相依套件。")
        return 1

    print(f"[初始化] 開啟 {args.com} @ {args.baud} ...")
    try:
        ser = open_serial_for_setup(args.com, args.baud, timeout=0.5)
    except serial.SerialException as exc:
        print(f"[錯誤] 無法開啟 {args.com}: {exc}")
        print("[提示] 請確認 ESP32 已連接且序列埠未被其他程式佔用。")
        return 1

    try:
        print("[初始化] 重啟 ESP32 ...")
        reset_esp32(ser)

        state = read_until_prompt_or_csi(ser, args.setup_timeout)

        if state == "prompt":
            ssid, password = prompt_credentials(args)
            print("[傳送] 正在傳送 Wi-Fi 帳密 ...")
            ser.write(format_wifi_line(ssid, password, LINE_ENDINGS[args.wifi_line_ending]).encode("utf-8"))
            ser.flush()
            if not wait_for_csi(ser, args.connect_timeout):
                print("[錯誤] 傳送 Wi-Fi 帳密後等待 CSI_DATA 逾時。")
                print("[提示] 請檢查 SSID/密碼並確認 ESP32 能連到該 AP。")
                print_wifi_timeout_hints(ssid)
                return 1
        elif state == "csi":
            print("[成功] CSI_DATA 已在傳輸中。")
        else:
            print("[錯誤] 未收到 Wi-Fi 提示或 CSI_DATA。")
            print("[提示] 請按一下 ESP32 上的 EN 鍵再重試。")
            return 1
    finally:
        release_serial_control_lines(ser)
        ser.close()

    launch_gui(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
