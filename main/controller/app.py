"""Telegram remote control for the CookieRun Classic bot. See README.md.

Component layout (shopping-pipeline style):
- models/       — start-flag domain: defaults, parsing, validation
- views/        — keyboards + message text (presentation, no I/O)
- services/     — Telegram transport, device reads, bot child process
- repositories/ — on-disk state: bot logs, last-start flags
- app.py        — this file: command handlers, callback routing, main loop

The names imported from the layers are re-exported on purpose: handlers and
the Controller below reference them through this module's namespace, which
keeps every external call in one place (and the tests patch them here).
"""

import atexit
import signal
import sys
import threading
import time

from services.adb import device_connect, device_ensure_app_running, device_force_stop_app
from config import get_settings

from .models.flags import (
    BOOST_COUNT,
    BOOST_NAMES,
    DEFAULT_FLAGS,
    flags_to_answers,
    flags_to_stdin,
    parse_start_args,
    validate_flags,
)
from .repositories.last_start import (
    LAST_START_FILE,
    _flags_from_saved_or_default,
    _load_last_start,
    _save_last_start,
)
from .repositories.logs import (
    LOG_DIR,
    LOG_FILE,
    LOG_FILE_PREV,
    LOG_TAIL_DEFAULT,
    LOG_TAIL_MAX,
    _rotate_log,
    _tail_log,
)
from .services.bot_process import BotProcess
from .services.device import DEVICE_IP, DEVICE_PORT, GAME_PACKAGE, capture_screenshot_bytes
from .services.telegram import (
    POLL_TIMEOUT,
    _message_id_from_result,
    answer_callback_query,
    edit_message_reply_markup,
    get_updates,
    send_message,
    send_photo,
)
from .views.keyboards import board_keyboard, menu_keyboard, resume_keyboard, session_keyboard
from .views.messages import (
    GUIDE_TEXT,
    HELP_TEXT,
    MENU_TEXT,
    _START_USAGE,
    _format_duration,
    board_text,
    flags_summary,
)

_SETTINGS = get_settings()
TELEGRAM_BOT_TOKEN = _SETTINGS.telegram_bot_token
TELEGRAM_ALLOWED_USER_IDS = set(_SETTINGS.telegram_allowed_user_ids)


def send_code_message(chat_id, header, code_text, timeout=10):
    """Send `header` as plain text followed by `code_text` in a monospace
    block, so log-style lines (timestamps + emoji + text) stay aligned the
    way they do in a terminal instead of reflowing in Telegram's
    proportional font. Lives in the controller layer (not view.py) so its
    send goes through the same send_message the handlers use."""
    escaped = code_text.replace("`", "'")
    text = f"{header}\n```\n{escaped}\n```" if header else f"```\n{escaped}\n```"
    send_message(chat_id, text, timeout=timeout, parse_mode="Markdown")


_MENU_COMMANDS = {
    "status": "/status",
    "logs": "/logs",
    "screenshot": "/screenshot",
    "pause": "/pause",
    "stop": "/stop",
    "help": "/help",
}


# -------------------
# COMMAND HANDLERS
# -------------------
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
    # Flag/stdin jargon stays in the controller log (useful for the prompt
    # coupling check); the Telegram reply speaks plain language.
    flag_str = " ".join(f"{k}={v}" for k, v in flags.items())
    answers_str = " ".join(flags_to_answers(flags))
    print(f"🚦 /start flags: {flag_str}  →  stdin: {answers_str}")
    controller._retire_session_panels()  # stale panels must not control the new run
    result = send_message(
        chat_id,
        f"▶️ Started (pid {controller.bot.proc.pid})\n{flags_summary(flags)}",
        reply_markup=session_keyboard(),
    )
    controller.session_messages[chat_id] = _message_id_from_result(result)


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
        controller._send_resume_button(chat_id, "📱 Closed CookieRun Classic.", paused=False)
        return True
    except Exception as e:
        controller._retire_session_panels()
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
        # chat_id -> message_id of the action panel attached to the latest
        # ▶️ Started message. Same retirement rules as boards: a new session
        # or a stop/kill strips the old panel's buttons.
        self.session_messages = {}
        # chat_id -> message_id of the ▶️ Continue / ▶️ Start button sent
        # after pause/stop/exit. Retired alongside the session panels.
        self.resume_messages = {}
        self._retired_session_messages = set()
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

    def _retire_session_panels(self):
        """Strip the buttons off every live session panel and resume button,
        in every chat. The bot session is global, so a button left clickable
        anywhere could pause/stop/start a run it doesn't belong to."""
        for messages in (self.session_messages, self.resume_messages):
            for chat_id, message_id in messages.items():
                if message_id is not None:
                    self._retired_session_messages.add((chat_id, message_id))
                    edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": []})
            messages.clear()

    def _send_resume_button(self, chat_id, text, paused):
        """Send the post-exit message with its one-tap recovery button,
        replacing any resume button sent earlier."""
        self._retire_session_panels()  # one live resume button at a time
        result = send_message(chat_id, text, reply_markup=resume_keyboard(paused))
        message_id = _message_id_from_result(result)
        if message_id is not None:
            self.resume_messages[chat_id] = message_id

    def _resume_continue(self, chat_id):
        """▶️ Continue after /pause: relaunch with the flags of the session
        that just paused (persisted as last-start when it began)."""
        self._retire_session_panels()  # button consumed
        _launch_with_flags(self, chat_id, _flags_from_saved_or_default())

    def _resume_board(self, chat_id):
        """▶️ Start after /stop: open the option board, same as a bare /start."""
        self._retire_session_panels()  # button consumed
        self.show_start_board(chat_id, _flags_from_saved_or_default())

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
            if (chat_id, message_id) in self._retired_session_messages:
                return  # late tap on a panel from a session that already ended
            if data == "toggle:rb":
                self._toggle_board_rb(chat_id, message_id)
            elif data.startswith("toggle:"):
                self._toggle_board_flag(chat_id, message_id, data.split(":", 1)[1])
            elif data.startswith("setrb:"):
                self._set_board_rb(chat_id, message_id, data.split(":", 1)[1])
            elif data == "board:start":
                self._start_from_board(chat_id)
            elif data == "resume:continue":
                self._resume_continue(chat_id)
            elif data == "resume:board":
                self._resume_board(chat_id)
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
                    self._send_resume_button(
                        self.pause_chat_id,
                        "⏸️ PAUSED — bot taps stopped; CookieRun Classic is still open.",
                        paused=True,
                    )
                self.pause_chat_id = None
                return
            print(f"🔴 Bot process exited (code {code}).")
            if self.last_chat_id is not None:
                self._send_resume_button(self.last_chat_id, f"🔴 Bot process exited (code {code}). Check /logs.", paused=False)

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
