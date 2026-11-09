"""Telegram remote control for the CookieRun Classic bot.

This lets you run the bot's terminal remotely from Telegram (e.g. from your
phone) while keeping bot automation in its own child process:

    /start [fs=y|n cr=y|n rb=1-11|n relic=y|n]  -> launches CookieRun
                                                    Classic via ADB (if not
                                                    already running), spawns
                                                    `python3 -u main.py`, and
                                                    pipes the answers to its
                                                    interactive prompts over
                                                    stdin
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
prompt_user_options() (bot.py:113-129). If those prompts are ever reordered,
added to, or removed, this mapping must be updated to match, or /start will
feed answers to the wrong questions. After every /start, check /logs for the
"✅ Selected: <boost>" line (bot.py:126) to confirm the answers landed where
expected.

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

# Keep in sync with len(BOOST_CHOICES) in bot.py:67-79.
BOOST_COUNT = 11

DEFAULT_FLAGS = {"fs": "n", "cr": "n", "rb": "n", "relic": "n"}

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# -------------------
# TELEGRAM TRANSPORT (stdlib only, mirrors alerts.py's style)
# -------------------
def _api_url(method):
    return f"{API_BASE}/{method}"


def send_message(chat_id, text, timeout=10, parse_mode=None):
    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _api_url("sendMessage"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=timeout)
    except (socket.gaierror, urllib.error.URLError) as e:
        print(f"⚠️ Couldn't send Telegram message — connection issue: {e}")
    except Exception as e:
        print(f"⚠️ Failed to send Telegram message: {e}")


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
            raise ValueError(f"Unknown flag '{key}'. Valid flags: fs, cr, rb, relic")
        flags[key] = value
    return flags


def validate_flags(flags):
    errors = []
    for key in ("fs", "cr", "relic"):
        if flags[key] not in ("y", "n"):
            errors.append(f"{key} must be y or n (got '{flags[key]}')")
    rb = flags["rb"]
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
HELP_TEXT = (
    "🍪 CookieRun Bot Controller\n\n"
    "/start [fs=y|n cr=y|n rb=1-11|n relic=y|n] — launches CookieRun Classic "
    "(if not already running) then starts the bot (reuses last flags for "
    "anything omitted)\n"
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
    "relic=y|n — Detect Relic: open + claim relics when seen\n\n"
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
    "Example: /start fs=y cr=y rb=1 relic=n"
)


def cmd_start(controller, chat_id, args_text):
    if controller.pause_pending:
        send_message(chat_id, "⏳ Bot is still pausing. Wait for the PAUSED message before /start.")
        return
    if controller.bot.is_running():
        send_message(chat_id, "⚠️ Bot is already running. Use /stop first.")
        return
    base = _load_last_start() or dict(DEFAULT_FLAGS)
    try:
        flags = parse_start_args(args_text, base)
        validate_flags(flags)
    except ValueError as e:
        send_message(chat_id, f"❌ {e}\n\nUsage: /start fs=y cr=y rb=1 relic=n")
        return
    send_message(chat_id, "📱 Launching CookieRun Classic...")
    try:
        device_ensure_app_running(DEVICE_IP, DEVICE_PORT, GAME_PACKAGE)
    except Exception as e:
        send_message(chat_id, f"❌ Couldn't launch CookieRun Classic: {e}")
        return
    controller.bot.start(flags)
    controller.last_known_alive = True
    controller.paused = False
    flag_str = " ".join(f"{k}={v}" for k, v in flags.items())
    answers_str = " ".join(flags_to_answers(flags))
    send_message(
        chat_id,
        f"▶️ Started (pid {controller.bot.proc.pid})\n{flag_str}  →  stdin: {answers_str}",
    )


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
                if "message" in update:
                    self._handle_message(update["message"])
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
