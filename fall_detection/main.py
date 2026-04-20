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
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# .env 放在專案根目錄（上一層）
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
WIFI_DEFAULT_SSID_ENV = "WIFI_DEFAULT_SSID"
WIFI_DEFAULT_PASSWORD_ENV = "WIFI_DEFAULT_PASSWORD"


def load_env_file(path: Path = ENV_FILE) -> None:
    """讀取 .env 檔案並設定環境變數。"""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


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
            "  .env -> WIFI_DEFAULT_SSID / WIFI_DEFAULT_PASSWORD"
        ),
    )
    parser.add_argument("--com", default="COM3", help="ESP32 序列埠 (預設: COM3)")
    parser.add_argument("--baud", type=int, default=921600, help="ESP32 鮑率 (預設: 921600)")
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
    parser.add_argument("--skip-setup", action="store_true", help="跳過 Wi-Fi 設定，直接啟動 GUI")
    parser.add_argument("--simulate", action="store_true", help="使用模擬資料（不需要 ESP32）")
    # 偵測參數
    parser.add_argument("--sti-threshold", type=float, default=0.22, help="STI 門檻 (預設: 0.22)")
    parser.add_argument("--sim-threshold", type=float, default=0.65, help="相似度門檻 (預設: 0.65)")
    parser.add_argument("--duration-threshold", type=float, default=3.0, help="持續時間門檻 (預設: 3.0 秒)")
    return parser.parse_args()


def format_wifi_line(ssid: str, password: str) -> str:
    if any(ch.isspace() for ch in ssid):
        ssid = f'"{ssid}"'
    return f"{ssid} {password}\n" if password else f"{ssid}\n"


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
    """硬體重啟 ESP32。"""
    ser.setDTR(False)
    ser.setRTS(False)
    time.sleep(0.2)
    ser.reset_input_buffer()
    ser.setDTR(True)
    ser.setRTS(True)


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
        ser = serial.Serial(args.com, args.baud, timeout=0.5)
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
            ser.write(format_wifi_line(ssid, password).encode("utf-8"))
            ser.flush()
            if not wait_for_csi(ser, args.connect_timeout):
                print("[錯誤] 傳送 Wi-Fi 帳密後等待 CSI_DATA 逾時。")
                print("[提示] 請檢查 SSID/密碼並確認 ESP32 能連到該 AP。")
                return 1
        elif state == "csi":
            print("[成功] CSI_DATA 已在傳輸中。")
        else:
            print("[錯誤] 未收到 Wi-Fi 提示或 CSI_DATA。")
            print("[提示] 請按一下 ESP32 上的 EN 鍵再重試。")
            return 1
    finally:
        ser.close()

    launch_gui(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
