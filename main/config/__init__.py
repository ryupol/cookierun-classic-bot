"""Back-compatible config package.

Bot modules keep doing `from config import X`; the constants come from
config/defaults.py (static tuning) and config/factory.py (environment
settings via get_settings()). New code should prefer get_settings() over
the module-level constants.
"""

from .defaults import *  # noqa: F401,F403
from .factory import Settings, get_settings  # noqa: F401

_settings = get_settings()

# Legacy module-level constants, derived from the factory-built Settings.
DEVICE_IP = _settings.device_ip
DEVICE_PORT = _settings.device_port
TELEGRAM_BOT_TOKEN = _settings.telegram_bot_token
TELEGRAM_CHAT_ID = _settings.telegram_chat_id
TELEGRAM_ALLOWED_USER_IDS = set(_settings.telegram_allowed_user_ids)
