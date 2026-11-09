"""Telegram remote control for the CookieRun Classic bot.

This lets you run the bot's terminal remotely from Telegram (e.g. from your
phone) while keeping bot automation in its own child process:

    /start                                       -> inline-button board of the
                                                    current flags; ▶️ Start
                                                    launches with those flags
    /start [fs=y|n cr=y|n rb=1-11|n relic=y|n 7j=y|n]
                                                 -> launches CookieRun Classic
                                                    via ADB (if not already
                                                    running), spawns
                                                    `python3 -u main.py`, and
                                                    pipes the answers to its
                                                    interactive prompts over
                                                    stdin
    /menu                                        -> inline buttons for status,
                                                    logs, screenshot, pause,
                                                    and stop
    /stop                                        -> SIGINT to the whole
                                                     process group (same as
                                                     pressing Ctrl+C), then
                                                     closes CookieRun Classic
    /pause                                       -> SIGINT to the bot, leaving
                                                     CookieRun Classic open;
                                                     use /start to restart
    /kill                                        -> SIGKILL backstop, then
                                                     closes CookieRun Classic
    /status                                      -> running state + uptime
    /logs [n]                                    -> tail of logs/bot.log
    /screenshot                                  -> current BlueStacks frame,
                                                     captured independently of
                                                     whether the bot is running
    /guide                                       -> explains the /start flags
                                                     and boost numbers
    /help                                        -> command list

IMPORTANT COUPLING NOTE: the /start flag -> stdin mapping below hard-codes
the exact order and count of the input() prompts in bot.py's
prompt_user_options(). The prompts are Fast Start, Cookie Relay, Desired
Random Boost (plus a boost number when yes), Detect Relic, then 7 jumps
(use_opening_jumps, answered by the 7j flag) last. If those prompts are ever
reordered, added to, or removed, this mapping must be updated to match, or
/start will feed answers to the wrong questions. After every /start, check
/logs for the "✅ Selected: <boost>" line to confirm the answers landed
where expected.

Run with:
    python3 controller.py

Requires TELEGRAM_BOT_TOKEN and a non-empty TELEGRAM_ALLOWED_USER_IDS in
.env (see .env.example). Refuses to start without an allowlist so a leaked
token can't let a stranger spawn processes on this machine.
"""

import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from adb import device_connect, device_ensure_app_running, device_force_stop_app
from config import (
    DEVICE_IP,
    DEVICE_PORT,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_TOKEN,
)

# CookieRun Classic's Android package. Only used by /start (to launch it
# before spawning the bot) and /stop, /kill (to close it after signaling
# the bot process) — bot.py's own device_reset_app() calls are unaffected.
GAME_PACKAGE = "com.devsisters.crg"

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
LOG_FILE_PREV = os.path.join(LOG_DIR, "bot.log.1")
LAST_START_FILE = os.path.join(LOG_DIR, "last_start.json")

LOG_TAIL_DEFAULT = 30
LOG_TAIL_MAX = 100
POLL_TIMEOUT = 30  # seconds, Telegram long-poll

# Names come straight from bot.py so the board always shows the real boost list.
from bot import BOOST_CHOICES  # noqa: E402

BOOST_COUNT = len(BOOST_CHOICES)
BOOST_NAMES = [name for name, _ in BOOST_CHOICES]

DEFAULT_FLAGS = {"fs": "n", "cr": "n", "rb": "n", "relic": "n", "7j": "n"}

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# -------------------
# TELEGRAM TRANSPORT (stdlib only, mirrors alerts.py's style)
# -------------------
def _api_url(method):
    return f"{API_BASE}/{method}"


def _api_post(method, body, timeout=10):
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _api_url(method),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (socket.gaierror, urllib.error.URLError) as e:
        print(f"⚠️ Telegram {method} failed — connection issue: {e}")
    except Exception as e:
        print(f"⚠️ Telegram {method} failed: {e}")
    return None


def send_message(chat_id, text, timeout=10, parse_mode=None, reply_markup=None):
    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    return _api_post("sendMessage", body, timeout=timeout)


def edit_message_reply_markup(chat_id, message_id, reply_markup, timeout=10):
    body = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
    return _api_post("editMessageReplyMarkup", body, timeout=timeout)


def answer_callback_query(callback_query_id, timeout=10):
    return _api_post("answerCallbackQuery", {"callback_query_id": callback_query_id}, timeout=timeout)


def _message_id_from_result(result):
    if not isinstance(result, dict):
        return None
    message = result.get("result")
    if not isinstance(message, dict):
        return None
    return message.get("message_id")


def send_code_message(chat_id, header, code_text, timeout=10):
    """Send `header` as plain text followed by `code_text` in a monospace
    block, so log-style lines (timestamps + emoji + text) stay aligned the
    way they do in a terminal instead of reflowing in Telegram's
    proportional font. Falls back to a plain message if Markdown parsing
    fails (e.g. code_text contains an odd number of backticks)."""
    escaped = code_text.replace("`", "'")
    text = f"{header}\n```\n{escaped}\n```" if header else f"```\n{escaped}\n```"
    send_message(chat_id, text, timeout=timeout, parse_mode="Markdown")


def send_photo(chat_id, image_bytes, filename="screenshot.png", caption=None, timeout=20):
    boundary = "----CookieRunControllerBoundary"

    def field(name, value):
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    parts = [field("chat_id", chat_id)]
    if caption:
        parts.append(field("caption", caption))
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(image_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    request = urllib.request.Request(
        _api_url("sendPhoto"),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=timeout)
    except (socket.gaierror, urllib.error.URLError) as e:
        print(f"⚠️ Couldn't send Telegram photo — connection issue: {e}")
    except Exception as e:
        print(f"⚠️ Failed to send Telegram photo: {e}")


def get_updates(offset, timeout=POLL_TIMEOUT):
    """Long-poll Telegram for updates. Returns a list of updates, or None on
    failure (network drop, sleep/wake, Telegram hiccup, etc.) so the caller
    can back off instead of hammering the API in a tight loop."""
    params = urllib.parse.urlencode({"offset": offset, "timeout": timeout})
    url = f"{_api_url('getUpdates')}?{params}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout + 10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("result", [])
    except (socket.gaierror, urllib.error.URLError) as e:
        print(f"⚠️ Telegram poll failed (network): {e}")
        return None
    except Exception as e:
        print(f"⚠️ Telegram poll failed: {e}")
        return None


# -------------------
# SCREENSHOT (independent of the bot child — works even if it's dead/wedged)
# -------------------
def capture_screenshot_bytes(timeout=10):
    cmd = ["adb", "-s", f"{DEVICE_IP}:{DEVICE_PORT}", "exec-out", "screencap", "-p"]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=True)
        return result.stdout
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        device_connect(DEVICE_IP, DEVICE_PORT)
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=True)
        return result.stdout


# -------------------
# /start FLAG <-> STDIN MAPPING (see coupling note at top of file)
# -------------------
def flags_to_answers(flags):
    answers = [flags["fs"], flags["cr"]]
    if flags["rb"] == "n":
        answers.append("n")
    else:
        answers.append("y")
        answers.append(flags["rb"])
    answers.append(flags["relic"])
    answers.append(flags["7j"])
    return answers


def flags_to_stdin(flags):
    return "\n".join(flags_to_answers(flags)) + "\n"


def parse_start_args(args_text, base):
    flags = dict(base)
    for token in args_text.split():
        if "=" not in token:
            raise ValueError(f"Invalid flag '{token}'. Use key=value, e.g. fs=y")
        key, _, value = token.partition("=")
        key = key.strip().lower()
        value = value.strip().lower()
        if key not in DEFAULT_FLAGS:
            raise ValueError(f"Unknown flag '{key}'. Valid flags: fs, cr, rb, relic, 7j")
        flags[key] = value
    return flags


def validate_flags(flags):
    errors = []
    for key in ("fs", "cr", "relic", "7j"):
        value = flags.get(key)
        if value not in ("y", "n"):
            errors.append(f"{key} must be y or n (got '{value}')")
    rb = str(flags.get("rb"))
    if rb != "n" and not (rb.isdigit() and 1 <= int(rb) <= BOOST_COUNT):
        errors.append(f"rb must be 'n' or 1-{BOOST_COUNT} (got '{rb}')")
    if errors:
        raise ValueError("; ".join(errors))


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


def rb_label(value):
    """Board label suffix for the rb flag: the full boost name when on."""
    if value.isdigit() and 1 <= int(value) <= BOOST_COUNT:
        return f": {BOOST_NAMES[int(value) - 1]}"
    return ""


def board_text(flags):
    # Static by design: per-tap refreshes edit only the button markup, so
    # the message text never flickers. Button highlight carries the state.
    return "🍪 Start options — highlighted = ON. Tap to change, then ▶️ Start."


def _toggle_button(key, label, on):
    button = {"text": label, "callback_data": f"toggle:{key}"}
    if on:
        button["style"] = "primary"  # highlighted = selected
    return button


def _pack(buttons, per_row=2):
    """Group buttons `per_row` per keyboard row so they render narrower."""
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


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


MENU_TEXT = "🍪 Controller menu\nTap a button to run that command."

_MENU_COMMANDS = {
    "status": "/status",
    "logs": "/logs",
    "screenshot": "/screenshot",
    "pause": "/pause",
    "stop": "/stop",
}


# -------------------
# LOG HELPERS
# -------------------
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


def _format_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# -------------------
# BOT CHILD PROCESS
# -------------------
class BotProcess:
    def __init__(self):
        self.proc = None
        self.started_at = None
        self.flags = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, flags):
        if self.is_running():
            raise RuntimeError("Bot is already running.")
        stdin_sequence = flags_to_stdin(flags)
        _rotate_log()
        log_fp = open(LOG_FILE, "a", buffering=1)
        try:
            # -u: unbuffered stdout, so /logs isn't minutes stale.
            # start_new_session: own process group so /stop and /kill can
            # signal the whole tree, not just this one pid.
            self.proc = subprocess.Popen(
                [sys.executable, "-u", "main.py"],
                stdin=subprocess.PIPE,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
        finally:
            log_fp.close()  # child holds its own duplicated fd
        try:
            self.proc.stdin.write(stdin_sequence)
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        self.started_at = time.time()
        self.flags = flags
        _save_last_start(flags)

    def _signal(self, sig):
        if not self.is_running():
            return False
        try:
            os.killpg(os.getpgid(self.proc.pid), sig)
        except ProcessLookupError:
            pass
        return True

    def stop(self):
        return self._signal(signal.SIGINT)

    def kill(self):
        return self._signal(signal.SIGKILL)

    def uptime(self):
        if self.started_at is None:
            return 0
        return time.time() - self.started_at


# -------------------
# COMMAND HANDLERS
# -------------------
_START_USAGE = "Usage: /start fs=y cr=y rb=1 relic=n 7j=y|n"

HELP_TEXT = (
    "🍪 CookieRun Bot Controller\n\n"
    "/start — button board for the current flags; tap ▶️ Start to launch\n"
    "/start [fs=y|n cr=y|n rb=1-11|n relic=y|n 7j=y|n] — launches CookieRun "
    "Classic (if not already running) then starts the bot (reuses last flags "
    "for anything omitted)\n"
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


def _launch_with_flags(controller, chat_id, flags):
    if controller.pause_pending:
        send_message(chat_id, "⏳ Bot is still pausing. Wait for the PAUSED message before /start.")
        return
    if controller.bot.is_running():
        send_message(chat_id, "⚠️ Bot is already running. Use /stop first.")
        return
    try:
        validate_flags(flags)
    except ValueError as e:
        send_message(chat_id, f"❌ {e}\n\n{_START_USAGE}")
        return
    send_message(chat_id, "📱 Launching CookieRun Classic...")
    try:
        device_ensure_app_running(DEVICE_IP, DEVICE_PORT, GAME_PACKAGE)
    except Exception as e:
        send_message(chat_id, f"❌ Couldn't launch CookieRun Classic: {e}")
        return
    flags = dict(flags)
    controller.bot.start(flags)
    controller.last_known_alive = True
    controller.paused = False
    flag_str = " ".join(f"{k}={v}" for k, v in flags.items())
    answers_str = " ".join(flags_to_answers(flags))
    send_message(
        chat_id,
        f"▶️ Started (pid {controller.bot.proc.pid})\n{flag_str}  →  stdin: {answers_str}",
    )


def cmd_start(controller, chat_id, args_text):
    if controller.pause_pending:
        send_message(chat_id, "⏳ Bot is still pausing. Wait for the PAUSED message before /start.")
        return
    if controller.bot.is_running():
        send_message(chat_id, "⚠️ Bot is already running. Use /stop first.")
        return
    if not args_text.strip():
        controller.show_start_board(chat_id, _flags_from_saved_or_default())
        return
    try:
        flags = parse_start_args(args_text, _flags_from_saved_or_default())
        validate_flags(flags)
    except ValueError as e:
        send_message(chat_id, f"❌ {e}\n\n{_START_USAGE}")
        return
    _launch_with_flags(controller, chat_id, flags)


def cmd_menu(controller, chat_id, args_text):
    send_message(chat_id, MENU_TEXT, reply_markup=menu_keyboard())


def cmd_pause(controller, chat_id, args_text):
    if controller.pause_pending:
        send_message(chat_id, "⏳ Bot is still pausing.")
        return
    if controller.paused:
        send_message(chat_id, "⏸️ Bot is already paused. Use /start when ready.")
        return
    if not controller.bot.is_running():
        send_message(chat_id, "ℹ️ Bot is not running.")
        return
    if not controller.bot.stop():
        send_message(chat_id, "ℹ️ Bot process already exited. Check /status.")
        return
    controller.pause_pending = True
    controller.pause_chat_id = chat_id
    _wait_for_bot_exit(controller.bot, wait=10)
    controller._check_child_exit()
    if controller.pause_pending:
        send_message(chat_id, "⏳ Bot is still stopping. CookieRun Classic stays open; /status will show PAUSING.")


def cmd_stop(controller, chat_id, args_text):
    if controller.paused:
        if _stop_app_after_exit(controller, chat_id):
            controller.paused = False
        return
    if not controller.bot.is_running():
        send_message(chat_id, "ℹ️ Bot is not running.")
        return
    controller.pause_pending = False
    controller.pause_chat_id = None
    controller.bot.stop()
    send_message(chat_id, "🛑 Sent stop signal (Ctrl+C). Waiting for it to exit before closing the app...")
    _stop_app_after_exit(controller, chat_id)


def cmd_kill(controller, chat_id, args_text):
    if controller.paused:
        if _stop_app_after_exit(controller, chat_id, wait=2):
            controller.paused = False
        return
    if not controller.bot.is_running():
        send_message(chat_id, "ℹ️ Bot is not running.")
        return
    controller.pause_pending = False
    controller.pause_chat_id = None
    controller.bot.kill()
    send_message(chat_id, "☠️ Sent SIGKILL.")
    _stop_app_after_exit(controller, chat_id, wait=2)


def _wait_for_bot_exit(bot, wait, poll_interval=0.5):
    deadline = time.monotonic() + wait
    while bot.is_running() and time.monotonic() < deadline:
        time.sleep(poll_interval)


def _stop_app_after_exit(controller, chat_id, wait=10, poll_interval=1):
    """Wait briefly for the bot process to actually exit (so it isn't still
    tapping the screen while we force-stop the app out from under it), then
    close CookieRun Classic. Proceeds with the force-stop regardless of
    whether the process exited in time."""
    _wait_for_bot_exit(controller.bot, wait, poll_interval)
    try:
        device_force_stop_app(DEVICE_IP, DEVICE_PORT, GAME_PACKAGE)
        send_message(chat_id, "📱 Closed CookieRun Classic.")
        return True
    except Exception as e:
        send_message(chat_id, f"⚠️ Bot stopped, but couldn't close CookieRun Classic: {e}")
        return False


def cmd_status(controller, chat_id, args_text):
    if controller.pause_pending:
        send_message(chat_id, "⏳ PAUSING — waiting for bot process to exit; game stays open")
    elif controller.paused:
        send_message(chat_id, "⏸️ PAUSED — game is open; use /start to restart automation")
    elif controller.bot.is_running():
        uptime = _format_duration(controller.bot.uptime())
        lines = _tail_log(1)
        last_line = lines[0] if lines else "(no output yet)"
        send_code_message(
            chat_id,
            header=f"🟢 RUNNING (pid {controller.bot.proc.pid}) — uptime {uptime}",
            code_text=last_line,
        )
    else:
        send_message(chat_id, "🔴 STOPPED")


def cmd_logs(controller, chat_id, args_text):
    n = LOG_TAIL_DEFAULT
    if args_text.strip():
        try:
            n = int(args_text.strip())
        except ValueError:
            send_message(chat_id, "❌ /logs expects a number, e.g. /logs 50")
            return
    n = max(1, min(n, LOG_TAIL_MAX))
    lines = _tail_log(n)
    if not lines:
        send_message(chat_id, "📋 (log is empty)")
        return
    text = "\n".join(lines)
    if len(text) > 3500:  # stay well under Telegram's ~4096 char message limit
        text = text[-3500:]
    send_code_message(chat_id, header=f"📋 Last {len(lines)} line(s):", code_text=text)


def cmd_screenshot(controller, chat_id, args_text):
    try:
        image_bytes = capture_screenshot_bytes()
    except Exception as e:
        send_message(chat_id, f"❌ Screenshot failed: {e}")
        return
    send_photo(chat_id, image_bytes, caption="📸 Current BlueStacks screen")


def cmd_help(controller, chat_id, args_text):
    send_message(chat_id, HELP_TEXT)


def cmd_guide(controller, chat_id, args_text):
    send_message(chat_id, GUIDE_TEXT)


COMMANDS = {
    "/start": cmd_start,
    "/menu": cmd_menu,
    "/pause": cmd_pause,
    "/stop": cmd_stop,
    "/kill": cmd_kill,
    "/status": cmd_status,
    "/logs": cmd_logs,
    "/screenshot": cmd_screenshot,
    "/guide": cmd_guide,
    "/help": cmd_help,
}


# -------------------
# CONTROLLER MAIN LOOP
# -------------------
class Controller:
    def __init__(self):
        self.bot = BotProcess()
        self.offset = 0
        self.last_chat_id = None
        self.last_known_alive = False
        self.pause_pending = False
        self.paused = False
        self.pause_chat_id = None
        # chat_id -> {"message_id": int | None, "flags": dict}
        # In memory only. Restarting the controller resets the board.
        self.boards = {}
        # (chat_id, message_id) of boards whose keyboard was stripped when a
        # newer board replaced them or Start consumed them. Late taps on
        # those messages are ignored.
        self._retired_board_messages = set()
        # Callbacks are answered/refreshed off the polling loop (see
        # _handle_callback). Tests flip this off to run them synchronously.
        self._async_callbacks = True
        self._callback_lock = threading.Lock()

    def show_start_board(self, chat_id, flags):
        self._retire_board(chat_id)  # strip the previous board's keyboard
        flags = {key: flags.get(key, DEFAULT_FLAGS[key]) for key in DEFAULT_FLAGS}
        result = send_message(chat_id, board_text(flags), reply_markup=board_keyboard(flags))
        self.boards[chat_id] = {
            "message_id": _message_id_from_result(result),
            "flags": flags,
        }

    def _retire_board(self, chat_id):
        """Remove the board's buttons so it can no longer be tapped."""
        board = self.boards.pop(chat_id, None)
        if not board:
            return
        message_id = board.get("message_id")
        if message_id is not None:
            self._retired_board_messages.add((chat_id, message_id))
            edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})

    def _board_for(self, chat_id, message_id):
        board = self.boards.get(chat_id)
        if board is None:
            board = {"message_id": message_id, "flags": _flags_from_saved_or_default()}
            self.boards[chat_id] = board
        elif message_id is not None and board.get("message_id") is not None and message_id != board["message_id"]:
            return None  # tap on a superseded board message
        elif message_id is not None:
            board["message_id"] = message_id
        return board

    def _refresh_board(self, chat_id, board):
        """Markup-only refresh: the board text is static, so a tap costs one
        editMessageReplyMarkup instead of a full text rewrite."""
        message_id = board.get("message_id")
        if message_id is None:
            return
        edit_message_reply_markup(chat_id, message_id, board_keyboard(board["flags"]))

    def _toggle_board_flag(self, chat_id, message_id, key):
        if key not in DEFAULT_FLAGS or key == "rb":
            return
        board = self._board_for(chat_id, message_id)
        if board is None:
            return
        current = board["flags"].get(key, "n")
        board["flags"][key] = "n" if current == "y" else "y"
        self._refresh_board(chat_id, board)

    def _toggle_board_rb(self, chat_id, message_id):
        """Random Boost main button: off -> on (default Double Coins, picker
        expands); on -> off (picker collapses)."""
        board = self._board_for(chat_id, message_id)
        if board is None:
            return
        current = board["flags"].get("rb", "n")
        board["flags"]["rb"] = "1" if current == "n" else "n"
        self._refresh_board(chat_id, board)

    def _set_board_rb(self, chat_id, message_id, value):
        """Radio-select one boost by number; invalid values are ignored."""
        if not (value.isdigit() and 1 <= int(value) <= BOOST_COUNT):
            return
        board = self._board_for(chat_id, message_id)
        if board is None:
            return
        board["flags"]["rb"] = value
        self._refresh_board(chat_id, board)

    def _start_from_board(self, chat_id):
        board = self.boards.get(chat_id)
        flags = dict(board["flags"]) if board else _flags_from_saved_or_default()
        self._retire_board(chat_id)  # consumed: strip its buttons
        _launch_with_flags(self, chat_id, flags)

    def _dispatch_menu(self, chat_id, action):
        command = _MENU_COMMANDS.get(action)
        if command is None:
            send_message(chat_id, f"❓ Unknown menu action: {action}")
            return
        COMMANDS[command](self, chat_id, "")

    def _handle_callback(self, callback):
        data = callback.get("data") or ""
        user_id = (callback.get("from") or {}).get("id")
        if not self.is_authorized(user_id):
            print(f"⛔ Rejected callback from unauthorized user_id={user_id}: {data!r}")
            return
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        callback_id = callback.get("id")
        if chat_id is None:
            return
        # Telegram API round trips measured at ~0.85s each from this host, so
        # doing answer+edit inline made every tap feel ~2s. Fire both off the
        # polling loop: the answer in its own thread (clears the button
        # spinner), the state change + board refresh in another.
        if callback_id:
            threading.Thread(target=answer_callback_query, args=(callback_id,), daemon=True).start()
        args = (chat_id, message.get("message_id"), data)
        if self._async_callbacks:
            threading.Thread(target=self._run_callback, args=args, daemon=True).start()
        else:
            self._run_callback(*args)

    def _run_callback(self, chat_id, message_id, data):
        # Serialized: taps are infrequent, and ▶️ Start vs /start must never race.
        with self._callback_lock:
            self._check_child_exit()
            self.last_chat_id = chat_id
            if (chat_id, message_id) in self._retired_board_messages:
                return  # late tap on a board whose buttons were stripped
            if data == "toggle:rb":
                self._toggle_board_rb(chat_id, message_id)
            elif data.startswith("toggle:"):
                self._toggle_board_flag(chat_id, message_id, data.split(":", 1)[1])
            elif data.startswith("setrb:"):
                self._set_board_rb(chat_id, message_id, data.split(":", 1)[1])
            elif data == "board:start":
                self._start_from_board(chat_id)
            elif data.startswith("menu:"):
                self._dispatch_menu(chat_id, data.split(":", 1)[1])

    def _dispatch_update(self, update):
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        if "message" in update:
            self._handle_message(update["message"])

    def is_authorized(self, user_id):
        return user_id in TELEGRAM_ALLOWED_USER_IDS

    def _drain_backlog(self):
        updates = get_updates(self.offset, timeout=0) or []
        for update in updates:
            self.offset = update["update_id"] + 1
        if updates:
            print(f"🧹 Drained {len(updates)} pending Telegram update(s) from before startup.")

    def _check_child_exit(self):
        proc = self.bot.proc
        if proc is not None and (self.last_known_alive or self.pause_pending) and proc.poll() is not None:
            self.last_known_alive = False
            code = proc.returncode
            if self.pause_pending:
                self.pause_pending = False
                self.paused = True
                print(f"⏸️ Bot process paused (exit code {code}).")
                if self.pause_chat_id is not None:
                    send_message(self.pause_chat_id, "⏸️ PAUSED — bot taps stopped; CookieRun Classic is still open. Use /start when ready.")
                self.pause_chat_id = None
                return
            print(f"🔴 Bot process exited (code {code}).")
            if self.last_chat_id is not None:
                send_message(self.last_chat_id, f"🔴 Bot process exited (code {code}). Check /logs.")

    def _handle_message(self, message):
        text = message.get("text", "")
        if not text.startswith("/"):
            return
        user_id = message.get("from", {}).get("id")
        chat_id = message["chat"]["id"]
        if not self.is_authorized(user_id):
            print(f"⛔ Rejected command from unauthorized user_id={user_id}: {text!r}")
            return
        self._check_child_exit()
        self.last_chat_id = chat_id
        parts = text.strip().split(maxsplit=1)
        command = parts[0].lower().split("@")[0]
        args_text = parts[1] if len(parts) > 1 else ""
        handler = COMMANDS.get(command)
        if handler is None:
            send_message(chat_id, f"❓ Unknown command: {command}\n\n{HELP_TEXT}")
            return
        try:
            handler(self, chat_id, args_text)
        except Exception as e:
            send_message(chat_id, f"❌ Error handling {command}: {e}")

    def run(self):
        if not TELEGRAM_BOT_TOKEN:
            print("❌ TELEGRAM_BOT_TOKEN is not set in .env. Exiting.")
            return
        if not TELEGRAM_ALLOWED_USER_IDS:
            print("❌ TELEGRAM_ALLOWED_USER_IDS is empty. Refusing to start — set it in .env first.")
            return
        try:
            device_connect(DEVICE_IP, DEVICE_PORT)
        except Exception as e:
            print(f"⚠️ Initial ADB connect failed (will retry on first /screenshot or /start): {e}")

        print(f"🤖 Controller started. Allowed user id(s): {sorted(TELEGRAM_ALLOWED_USER_IDS)}")
        self._drain_backlog()
        consecutive_failures = 0
        while True:
            updates = get_updates(self.offset)
            if updates is None:
                # Network hiccup (common right after the Mac wakes from
                # sleep) — back off exponentially instead of hammering
                # Telegram every loop, and stay quiet after the first
                # few retries so a prolonged outage doesn't spam the log.
                consecutive_failures += 1
                wait = min(5 * (2 ** (consecutive_failures - 1)), 120)
                if consecutive_failures <= 3:
                    print(f"⏳ Retrying in {wait}s...")
                time.sleep(wait)
                continue
            consecutive_failures = 0
            for update in updates:
                self.offset = update["update_id"] + 1
                self._dispatch_update(update)
            self._check_child_exit()


def _cleanup(controller):
    if controller.bot.is_running():
        controller.bot.kill()


def main():
    controller = Controller()
    atexit.register(_cleanup, controller)

    def _on_signal(signum, frame):
        print("\n🛑 Controller shutting down...")
        if controller.bot.is_running():
            controller.bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    # tmux kill-session sends SIGHUP to the pane process. Without a handler,
    # Python terminates immediately and skips atexit cleanup, orphaning the
    # detached bot subprocess (it has its own process group via
    # start_new_session=True, so it survives tmux's teardown otherwise).
    signal.signal(signal.SIGHUP, _on_signal)
    controller.run()


if __name__ == "__main__":
    main()
