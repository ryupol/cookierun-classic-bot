"""Inline keyboard builders for the option board, the /menu keyboard, the
session action panel, and the post-exit recovery button."""

from ..models.flags import BOOST_NAMES


def _toggle_button(key, label, on):
    button = {"text": label, "callback_data": f"toggle:{key}"}
    if on:
        button["style"] = "primary"  # highlighted = selected
    return button


def _pack(buttons, per_row=2):
    """Group buttons `per_row` per keyboard row so they render narrower."""
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def rb_label(value):
    """Board label suffix for the rb flag: the full boost name when on."""
    if value.isdigit() and 1 <= int(value) <= len(BOOST_NAMES):
        return f": {BOOST_NAMES[int(value) - 1]}"
    return ""


def board_keyboard(flags):
    toggles = [
        _toggle_button(key, label, flags[key] == "y")
        for key, label in (
            ("fs", "⚡ Fast Start"),
            ("cr", "🍪 Cookie Relay"),
            ("relic", "🏺 Relic"),
            ("7j", "🐇 7 Jumps"),
        )
    ]
    rows = _pack(toggles)
    rows.append([_toggle_button("rb", f"🎲 Random Boost{rb_label(flags['rb'])}", flags["rb"] != "n")])
    if flags["rb"] != "n":
        # Boost picker: radio-style, exactly one selected (highlighted);
        # bulleted so the rows read as a nested list under Random Boost.
        picker = []
        for i, name in enumerate(BOOST_NAMES, 1):
            selected = flags["rb"] == str(i)
            mark = "✅ " if selected else "▫️ "
            button = {"text": f"{mark}{name}", "callback_data": f"setrb:{i}"}
            if selected:
                button["style"] = "primary"
            picker.append(button)
        rows += _pack(picker)
    rows.append([{"text": "▶️ Start", "callback_data": "board:start", "style": "success"}])
    return {"inline_keyboard": rows}


def menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "menu:status"},
                {"text": "📋 Logs", "callback_data": "menu:logs"},
            ],
            [{"text": "📸 Screenshot", "callback_data": "menu:screenshot"}],
            [
                {"text": "⏸️ Pause", "callback_data": "menu:pause"},
                {"text": "🛑 Stop", "callback_data": "menu:stop"},
            ],
        ]
    }


def session_keyboard():
    """Action panel attached to the ▶️ Started message. Lives only as long
    as the session: a new /start strips the old panel's buttons (see
    Controller._retire_session_panels) so stale taps can't hit a new run."""
    return {
        "inline_keyboard": [
            [
                {"text": "⏸️ Pause", "callback_data": "menu:pause"},
                {"text": "🛑 Stop", "callback_data": "menu:stop"},
            ],
            [
                {"text": "📸 Screenshot", "callback_data": "menu:screenshot"},
                {"text": "📋 Logs", "callback_data": "menu:logs"},
            ],
            [
                {"text": "📊 Status", "callback_data": "menu:status"},
                {"text": "❓ Help", "callback_data": "menu:help"},
            ],
        ]
    }


def resume_keyboard(paused):
    """One-tap recovery after the bot exits: ▶️ Continue (same flags) when
    paused — the game is still open — or ▶️ Start (the option board, like a
    bare /start) when the session fully ended."""
    if paused:
        button = {"text": "▶️ Continue (same settings)", "callback_data": "resume:continue", "style": "success"}
    else:
        button = {"text": "▶️ Start", "callback_data": "resume:board", "style": "success"}
    return {"inline_keyboard": [[button]]}
