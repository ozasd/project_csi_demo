"""
告警管理器。

負責：
- 跌倒事件告警輸出
- 支援多種告警方式：控制台、音效、檔案記錄
- 告警冷卻機制（避免重複告警）
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AlertManager:
    """跌倒告警管理器。

    Parameters
    ----------
    cooldown_seconds : float
        告警冷卻時間（秒），同一事件不重複告警。
    log_file : str, optional
        告警記錄檔路徑。若提供則同時寫入檔案。
    enable_sound : bool
        是否啟用系統提示音。
    """

    def __init__(
        self,
        cooldown_seconds: float = 30.0,
        log_file: Optional[str] = None,
        enable_sound: bool = True,
    ):
        self.cooldown_seconds = cooldown_seconds
        self.log_file = log_file
        self.enable_sound = enable_sound

        self._last_alert_time: float = 0.0
        self._alert_count: int = 0
        self._callbacks: list = []

    def register_callback(self, callback) -> None:
        """註冊自訂告警回呼函式。

        callback 簽名：callback(alert_info: dict) -> None
        """
        self._callbacks.append(callback)

    def trigger_alert(self, state_dict: dict) -> bool:
        """觸發跌倒告警。

        Parameters
        ----------
        state_dict : dict
            來自 EventDetector.get_state_dict() 的狀態資訊。

        Returns
        -------
        triggered : bool
            是否成功觸發告警（冷卻期間內不觸發）。
        """
        now = time.time()

        # 冷卻機制
        if now - self._last_alert_time < self.cooldown_seconds:
            logger.debug("Alert cooldown active, skipping. (%.1fs remaining)",
                         self.cooldown_seconds - (now - self._last_alert_time))
            return False

        self._last_alert_time = now
        self._alert_count += 1

        alert_info = {
            **state_dict,
            "alert_id": self._alert_count,
            "alert_time": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        }

        # 控制台告警
        self._console_alert(alert_info)

        # 音效告警
        if self.enable_sound:
            self._sound_alert()

        # 檔案記錄
        if self.log_file:
            self._file_alert(alert_info)

        # 自訂回呼
        for cb in self._callbacks:
            try:
                cb(alert_info)
            except Exception:
                logger.exception("Alert callback error")

        return True

    def _console_alert(self, alert_info: dict) -> None:
        """控制台告警輸出。"""
        msg = (
            "\n"
            "╔══════════════════════════════════════╗\n"
            "║     ⚠️  跌倒偵測警報  ⚠️              ║\n"
            "╠══════════════════════════════════════╣\n"
            f"║  時間: {alert_info.get('alert_time', 'N/A'):<28s} ║\n"
            f"║  STI:  {alert_info.get('sti', 0):<28.4f} ║\n"
            f"║  相似度: {alert_info.get('similarity', 0):<26.4f} ║\n"
            f"║  持續: {alert_info.get('high_similarity_duration', 0):<27.2f}s ║\n"
            f"║  告警次數: {alert_info.get('alert_id', 0):<24d} ║\n"
            "╚══════════════════════════════════════╝\n"
        )
        logger.warning(msg)
        print(msg)

    def _sound_alert(self) -> None:
        """系統提示音。"""
        try:
            import winsound
            # 三聲急促 beep
            for _ in range(3):
                winsound.Beep(1000, 300)
                time.sleep(0.1)
        except (ImportError, RuntimeError):
            # 非 Windows 或無音效裝置，fallback 到 bell character
            print("\a")

    def _file_alert(self, alert_info: dict) -> None:
        """寫入告警記錄檔。"""
        try:
            path = Path(self.log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert_info, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Failed to write alert log")

    @property
    def alert_count(self) -> int:
        return self._alert_count

    @property
    def last_alert_time(self) -> float:
        return self._last_alert_time
