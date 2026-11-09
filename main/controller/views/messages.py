"""User-facing message text and small formatters."""

from ..models.flags import BOOST_COUNT, BOOST_NAMES

_START_USAGE = (
    "Usage: /start — opens the button board (recommended); or "
    "/start fs=y cr=y rb=1 relic=n 7j=y|n to skip the board and launch now"
)

HELP_TEXT = (
    "🍪 CookieRun Bot Controller\n\n"
    "Buttons first: /start opens the option board, and every ▶️ Started "
    "message carries Pause/Stop/Screenshot/Logs/Status/Help buttons that "
    "die with the session (a new /start disables the old ones). Pausing "
    "offers ▶️ Continue (same settings); stopping offers ▶️ Start (the "
    "option board).\n\n"
    "/start — button board for the current flags; tap ▶️ Start to launch\n"
    "/start [fs=y|n cr=y|n rb=1-11|n relic=y|n 7j=y|n] — typed launch "
    "(reuses last flags for anything omitted)\n"
    "/menu — buttons for status, logs (30 lines), screenshot, pause, and stop\n"
    "/pause — stop bot taps, leave CookieRun Classic open; /start to restart\n"
    "/stop — graceful stop (same as Ctrl+C), then closes CookieRun Classic\n"
    "/kill — force stop (SIGKILL), then closes CookieRun Classic\n"
    "/status — running state, uptime, last log line\n"
    "/logs [n] — last n log lines (default 30, max 100)\n"
    "/screenshot — current BlueStacks screen\n"
    "/guide — explain the /start flags and boost numbers\n"
    "/help — this message"
)

GUIDE_TEXT = (
    "📖 /start flags\n\n"
    "fs=y|n — Fast Start: buy + use it\n"
    "cr=y|n — Cookie Relay: buy + use it\n"
    "rb=1-11|n — Desired Random Boost: buy + use the boost number below, "
    "or n to skip\n"
    "relic=y|n — Detect Relic: open + claim relics when seen\n"
    "7j=y|n — 7 jumps at the start of every run\n\n"
    "Bare /start opens a button board (seeded from the last start, or the "
    "defaults) instead of launching. Tap a flag to change it — Random Boost "
    "toggles on/off; when on, 11 named boost buttons appear — tap one to "
    "select it — then ▶️ Start.\n"
    "/menu opens buttons for status, logs, screenshot, pause, and stop.\n\n"
    "🎲 rb boost numbers\n"
    "1. Double Coins\n"
    "2. +15% Score Bonus\n"
    "3. -15% HP Drain\n"
    "4. Revive Once with 80 HP\n"
    "5. 70% Crush Chance\n"
    "6. +17% Base Speed\n"
    "7. Gold Coin Magic\n"
    "8. -30% Collision Damage\n"
    "9. +20% HP from Potions\n"
    "10. Magnetic Aura\n"
    "11. 2 Pit Lifts\n\n"
    "Example: /start fs=y cr=y rb=1 relic=n 7j=y"
)

MENU_TEXT = "🍪 Controller menu\nTap a button to run that command."


def flags_summary(flags):
    """Human-readable option states for the ▶️ Started confirmation."""
    def mark(value):
        return "✅" if value == "y" else "❌"

    lines = [
        f"⚡ Fast Start {mark(flags['fs'])}   🍪 Cookie Relay {mark(flags['cr'])}",
        f"🏺 Relic {mark(flags['relic'])}   🐇 7 Jumps {mark(flags['7j'])}",
    ]
    rb = flags["rb"]
    if rb.isdigit() and 1 <= int(rb) <= BOOST_COUNT:
        lines.append(f"🎲 Random Boost: {BOOST_NAMES[int(rb) - 1]} ✅")
    else:
        lines.append("🎲 Random Boost ❌")
    return "\n".join(lines)


def board_text(flags):
    # Static by design: per-tap refreshes edit only the button markup, so
    # the message text never flickers. Button highlight carries the state.
    return "🍪 Start options — highlighted = ON. Tap to change, then ▶️ Start."


def _format_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
