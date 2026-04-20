"""
Wi-Fi RSSI 動態偵測系統 — WiFi 掃描器
==========================================
負責透過 Windows netsh 指令收集 RSSI 資料。
支援已連線 AP 的高頻輪詢與周圍 AP 的全頻掃描。
"""

import subprocess
import re
import time
import threading
import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


def _decode_output(raw: bytes) -> str:
    """嘗試多種編碼解碼 netsh 輸出（相容各語系 Windows）

    注意：在繁體中文 Windows 上，OEM code page 通常是 cp950 (Big5)。
    必須先嘗試系統編碼，因為 utf-8 可能對 Big5 位元組「部分成功」
    但產生亂碼。
    """
    import locale
    # 取得系統偏好編碼（通常是 cp950 / cp936 等）
    sys_enc = locale.getpreferredencoding(False)
    # 優先順序：系統編碼 → cp950 → cp936 → utf-8 → latin-1
    encodings = [sys_enc, 'cp950', 'cp936', 'big5', 'utf-8', 'latin-1']
    # 去重但保持順序
    seen = set()
    unique_encodings = []
    for e in encodings:
        key = e.lower().replace('-', '').replace('_', '')
        if key not in seen:
            seen.add(key)
            unique_encodings.append(e)

    for enc in unique_encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('latin-1', errors='replace')


@dataclass
class APInfo:
    """單一 AP 的資訊"""
    bssid: str
    ssid: str
    signal_percent: int
    rssi_dbm: float
    channel: int = 0
    band: str = ''
    angle: float = 0.0          # 雷達上的角度 (rad)
    last_seen: float = 0.0
    is_connected: bool = False


@dataclass
class RSSISample:
    """單次 RSSI 取樣"""
    timestamp: float
    rssi_dbm: float
    signal_percent: int
    bssid: str
    ssid: str


class WiFiScanner:
    """Wi-Fi 訊號掃描器

    提供兩種掃描模式：
    1. 快速輪詢：只取已連線 AP 的 RSSI（~200ms）
    2. 全頻掃描：取所有可見 AP 的訊號（~2-3s）
    """

    def __init__(self):
        self.samples: deque[RSSISample] = deque(maxlen=2000)
        self.ap_map: dict[str, APInfo] = {}
        self.connected_bssid: Optional[str] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ──────────────────────────────────────────────
    #  公開 API
    # ──────────────────────────────────────────────

    def start(self, poll_interval: float = 0.4, scan_interval: float = 8.0):
        """啟動背景收集執行緒"""
        self._running = True
        self._poll_interval = poll_interval
        self._scan_interval = scan_interval
        self._thread = threading.Thread(target=self._collection_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止背景收集"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def get_latest_samples(self, n: int = 50) -> list[RSSISample]:
        """取得最近 N 筆取樣"""
        with self._lock:
            return list(self.samples)[-n:]

    def get_all_aps(self) -> dict[str, APInfo]:
        """取得所有已知 AP 資訊"""
        with self._lock:
            return dict(self.ap_map)

    def get_connected_rssi(self) -> Optional[float]:
        """取得已連線 AP 的最新 RSSI"""
        with self._lock:
            if self.samples:
                return self.samples[-1].rssi_dbm
        return None

    # ──────────────────────────────────────────────
    #  背景收集迴圈
    # ──────────────────────────────────────────────

    def _collection_loop(self):
        """背景執行緒主迴圈"""
        last_scan_time = 0

        while self._running:
            try:
                # 高頻：輪詢已連線 AP
                self._poll_connected()

                # 低頻：全頻掃描周圍 AP
                now = time.time()
                if now - last_scan_time >= self._scan_interval:
                    self._scan_all_aps()
                    last_scan_time = now

            except Exception as e:
                # 靜默處理錯誤，避免執行緒中斷
                pass

            time.sleep(self._poll_interval)

    # ──────────────────────────────────────────────
    #  netsh 指令解析
    # ──────────────────────────────────────────────

    def _poll_connected(self):
        """輪詢已連線 AP 的 RSSI（高頻）"""
        try:
            result = subprocess.run(
                ['netsh', 'wlan', 'show', 'interfaces'],
                capture_output=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode != 0:
                return

            output = _decode_output(result.stdout)

            # 解析 SSID
            ssid_match = re.search(r'SSID\s+:\s+(.+)', output)
            ssid = ssid_match.group(1).strip() if ssid_match else 'Unknown'

            # 解析 BSSID (AP BSSID / BSSID)
            bssid_match = re.search(r'(?:AP\s+)?BSSID\s+:\s+([\da-fA-F:]+)', output)
            bssid = bssid_match.group(1).strip() if bssid_match else '00:00:00:00:00:00'

            # 解析 RSSI (dBm) — 優先使用
            rssi_dbm = None
            rssi_match = re.search(r'[Rr]ssi\s+:\s+(-?\d+)', output)
            if rssi_match:
                rssi_dbm = float(rssi_match.group(1))

            # 解析 Signal % (訊號 / Signal)
            signal_percent = 0
            signal_match = re.search(r'(?:訊號|Signal)\s+:\s+(\d+)%', output)
            if signal_match:
                signal_percent = int(signal_match.group(1))
                # 如果沒有 RSSI dBm，從百分比推算
                if rssi_dbm is None:
                    rssi_dbm = (signal_percent / 2) - 100

            if rssi_dbm is None:
                return

            # 解析頻道
            channel = 0
            ch_match = re.search(r'(?:通道|Channel)\s+:\s+(\d+)', output)
            if ch_match:
                channel = int(ch_match.group(1))

            # 解析頻段
            band = ''
            band_match = re.search(r'(?:頻帶|Band)\s+:\s+(.+)', output)
            if band_match:
                band = band_match.group(1).strip()

            now = time.time()

            with self._lock:
                # 記錄取樣
                sample = RSSISample(
                    timestamp=now,
                    rssi_dbm=rssi_dbm,
                    signal_percent=signal_percent,
                    bssid=bssid,
                    ssid=ssid,
                )
                self.samples.append(sample)
                self.connected_bssid = bssid

                # 更新 AP 資訊
                if bssid not in self.ap_map:
                    self.ap_map[bssid] = APInfo(
                        bssid=bssid, ssid=ssid,
                        signal_percent=signal_percent,
                        rssi_dbm=rssi_dbm,
                        channel=channel, band=band,
                        angle=self._bssid_to_angle(bssid),
                        is_connected=True,
                    )
                ap = self.ap_map[bssid]
                ap.rssi_dbm = rssi_dbm
                ap.signal_percent = signal_percent
                ap.last_seen = now
                ap.is_connected = True

        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    def _scan_all_aps(self):
        """全頻掃描所有可見 AP（低頻）"""
        try:
            result = subprocess.run(
                ['netsh', 'wlan', 'show', 'networks', 'mode=bssid'],
                capture_output=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode != 0:
                return

            output = _decode_output(result.stdout)
            now = time.time()

            # 分割成每個 SSID 區塊
            # 格式: SSID N : name ... BSSID N : xx:xx:... 訊號 : NN%
            ssid_blocks = re.split(r'(?=SSID\s+\d+\s*:)', output)

            for block in ssid_blocks:
                if not block.strip():
                    continue

                ssid_match = re.search(r'SSID\s+\d+\s*:\s*(.*)', block)
                ssid = ssid_match.group(1).strip() if ssid_match else ''

                # 每個 SSID 下可能有多個 BSSID
                bssid_sections = re.split(r'(?=BSSID\s+\d+)', block)

                for section in bssid_sections:
                    bssid_match = re.search(
                        r'BSSID\s+\d+\s*:\s*([\da-fA-F:]+)', section
                    )
                    if not bssid_match:
                        continue

                    bssid = bssid_match.group(1).strip()

                    signal_match = re.search(
                        r'(?:訊號|Signal)\s*:\s*(\d+)%', section
                    )
                    signal_percent = int(signal_match.group(1)) if signal_match else 0
                    rssi_dbm = (signal_percent / 2) - 100

                    ch_match = re.search(
                        r'(?:通道|Channel)\s*:\s*(\d+)', section
                    )
                    channel = int(ch_match.group(1)) if ch_match else 0

                    with self._lock:
                        is_conn = (bssid == self.connected_bssid)
                        if bssid not in self.ap_map:
                            self.ap_map[bssid] = APInfo(
                                bssid=bssid, ssid=ssid,
                                signal_percent=signal_percent,
                                rssi_dbm=rssi_dbm,
                                channel=channel,
                                angle=self._bssid_to_angle(bssid),
                                is_connected=is_conn,
                            )
                        else:
                            ap = self.ap_map[bssid]
                            # 不覆蓋已連線 AP 的精確 RSSI
                            if not ap.is_connected:
                                ap.rssi_dbm = rssi_dbm
                            ap.signal_percent = signal_percent
                            ap.last_seen = now
                            ap.ssid = ssid or ap.ssid

        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    # ──────────────────────────────────────────────
    #  工具函式
    # ──────────────────────────────────────────────

    @staticmethod
    def _bssid_to_angle(bssid: str) -> float:
        """根據 BSSID 計算一個穩定的雷達角度（0 ~ 2π）"""
        import math
        h = int(hashlib.md5(bssid.encode()).hexdigest()[:8], 16)
        return (h % 3600) / 3600 * 2 * math.pi
