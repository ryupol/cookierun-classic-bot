"""Bot log files: rotation on start and tail reads for /logs and /status."""

import os

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
LOG_FILE_PREV = os.path.join(LOG_DIR, "bot.log.1")

LOG_TAIL_DEFAULT = 30
LOG_TAIL_MAX = 100


def _rotate_log():
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(LOG_FILE):
        try:
            os.replace(LOG_FILE, LOG_FILE_PREV)
        except OSError:
            pass


def _tail_log(n):
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", errors="replace") as f:
        lines = f.readlines()
    return [line.rstrip("\n") for line in lines[-n:]]
