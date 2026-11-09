"""Persistence for the flags used by the most recent /start, so bare
/start, the option board, and ▶️ Continue can all reuse them."""

import json
import os

from ..models.flags import DEFAULT_FLAGS
from .logs import LOG_DIR

LAST_START_FILE = os.path.join(LOG_DIR, "last_start.json")


def _save_last_start(flags):
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = LAST_START_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(flags, f)
    os.replace(tmp, LAST_START_FILE)


def _load_last_start():
    if not os.path.exists(LAST_START_FILE):
        return None
    try:
        with open(LAST_START_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _flags_from_saved_or_default():
    """Saved last-start flags, with any new flag filled from DEFAULT_FLAGS."""
    flags = dict(DEFAULT_FLAGS)
    saved = _load_last_start()
    if isinstance(saved, dict):
        for key in DEFAULT_FLAGS:
            if key in saved and saved[key] is not None:
                flags[key] = saved[key]
    return flags
