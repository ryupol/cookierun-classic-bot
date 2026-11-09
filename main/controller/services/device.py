"""Device reads that stay independent of the bot child — a screenshot must
work even if the bot process is dead or wedged."""

import subprocess

from services.adb import device_connect
from config import get_settings

# CookieRun Classic's Android package. Only used by /start (to launch it
# before spawning the bot) and /stop, /kill (to close it after signaling
# the bot process) — bot.py's own device_reset_app() calls are unaffected.
GAME_PACKAGE = "com.devsisters.crg"

_SETTINGS = get_settings()
DEVICE_IP = _SETTINGS.device_ip
DEVICE_PORT = _SETTINGS.device_port


def capture_screenshot_bytes(timeout=10):
    cmd = ["adb", "-s", f"{DEVICE_IP}:{DEVICE_PORT}", "exec-out", "screencap", "-p"]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=True)
        return result.stdout
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        device_connect(DEVICE_IP, DEVICE_PORT)
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=True)
        return result.stdout
