"""Config factory: builds an immutable Settings object from the environment.

Mirrors shopping-pipeline's per-env .conf + ConfigFactory style. Precedence:
os.environ > .env.<env> overlay > .env base. The overlay is selected by the
COOKIEBOT_ENV variable (e.g. COOKIEBOT_ENV=qa loads .env.qa), matching the
qa.conf / release.conf split in shopping-pipeline.

Static tuning data (templates, regions, coordinates) lives in
config/defaults.py — only environment-specific connection/secret settings
are factory-built here.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache

from .env import load_env


@dataclass(frozen=True)
class Settings:
    device_ip: str = "127.0.0.1"  # adb device address (BlueStacks by default)
    device_port: int = 5555
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_allowed_user_ids: frozenset = field(default_factory=frozenset)


def _parse_user_ids(raw):
    return frozenset(int(uid) for uid in raw.split(",") if uid.strip())


@lru_cache(maxsize=None)
def get_settings(env=None):
    """Build Settings for `env` (defaults to COOKIEBOT_ENV, else local .env).

    Loads the overlay first so .env only fills values the overlay didn't
    set (env.py populates via setdefault), giving the precedence above.
    Cached: repeat calls with the same env return the same object.
    """
    env = env or os.environ.get("COOKIEBOT_ENV", "")
    if env:
        load_env(f".env.{env}")
    load_env()
    return Settings(
        device_ip=os.environ.get("DEVICE_IP", Settings.device_ip),
        device_port=int(os.environ.get("DEVICE_PORT", str(Settings.device_port))),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        # Comma-separated numeric user IDs allowed to control the bot via the
        # controller (e.g. "111111111,222222222"). Message @userinfobot on
        # Telegram to find your own id. Empty disables remote control.
        telegram_allowed_user_ids=_parse_user_ids(os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")),
    )
