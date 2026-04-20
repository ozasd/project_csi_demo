"""語音提示工具。"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)


class VoiceNotifier:
    """以 Windows 內建 TTS 播放簡短中文語音提示。"""

    def __init__(
        self,
        fall_text: str,
        motion_text: str,
        fall_cooldown: float,
        motion_cooldown: float,
        enabled: bool = True,
    ):
        self.fall_text = fall_text
        self.motion_text = motion_text
        self.fall_cooldown = fall_cooldown
        self.motion_cooldown = motion_cooldown
        self.enabled = enabled and sys.platform == "win32"

        self._lock = threading.Lock()
        self._busy = False
        self._last_fall_at = 0.0
        self._last_motion_at = 0.0

    def speak_fall(self) -> bool:
        return self._queue_speech(
            text=self.fall_text,
            cooldown=self.fall_cooldown,
            kind="fall",
        )

    def speak_motion(self) -> bool:
        return self._queue_speech(
            text=self.motion_text,
            cooldown=self.motion_cooldown,
            kind="motion",
        )

    def _queue_speech(self, text: str, cooldown: float, kind: str) -> bool:
        if not self.enabled:
            return False

        now = time.time()
        with self._lock:
            last_at = self._last_fall_at if kind == "fall" else self._last_motion_at
            if now - last_at < cooldown or self._busy:
                return False

            self._busy = True
            if kind == "fall":
                self._last_fall_at = now
            else:
                self._last_motion_at = now

        threading.Thread(
            target=self._speak_worker,
            args=(text,),
            daemon=True,
        ).start()
        return True

    def _speak_worker(self, text: str) -> None:
        try:
            script = self._build_powershell_script(text)
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                check=False,
                timeout=15,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            logger.exception("Voice speech failed")
        finally:
            with self._lock:
                self._busy = False

    def _build_powershell_script(self, text: str) -> str:
        escaped = text.replace("'", "''")
        return (
            "Add-Type -AssemblyName System.Speech; "
            "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$speaker.Volume = 100; "
            "$speaker.Rate = 0; "
            f"$speaker.Speak('{escaped}')"
        )
