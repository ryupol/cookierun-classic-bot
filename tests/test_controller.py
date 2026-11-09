import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import controller


class SendCodeMessageTests(unittest.TestCase):
    @patch.object(controller, "send_message")
    def test_wraps_text_in_markdown_code_block(self, send_message):
        controller.send_code_message(123, "header", "line1\nline2")
        args, kwargs = send_message.call_args
        self.assertEqual(args[0], 123)
        self.assertIn("```\nline1\nline2\n```", args[1])
        self.assertTrue(args[1].startswith("header"))
        self.assertEqual(kwargs["parse_mode"], "Markdown")

    @patch.object(controller, "send_message")
    def test_omits_header_when_empty(self, send_message):
        controller.send_code_message(123, "", "line1")
        args, _ = send_message.call_args
        self.assertFalse(args[1].startswith("\n"))

    @patch.object(controller, "send_message")
    def test_escapes_backticks_to_avoid_breaking_code_block(self, send_message):
        controller.send_code_message(123, "header", "some `code` here")
        args, _ = send_message.call_args
        self.assertNotIn("`code`", args[1])
        self.assertIn("'code'", args[1])


class FlagParsingTests(unittest.TestCase):
    def test_flags_to_answers_with_boost(self):
        flags = {"fs": "y", "cr": "y", "rb": "1", "relic": "n", "7j": "n"}
        self.assertEqual(controller.flags_to_answers(flags), ["y", "y", "y", "1", "n", "n"])

    def test_flags_to_answers_without_boost(self):
        flags = {"fs": "n", "cr": "n", "rb": "n", "relic": "n", "7j": "n"}
        self.assertEqual(controller.flags_to_answers(flags), ["n", "n", "n", "n", "n"])

    def test_flags_to_answers_appends_7j_last(self):
        flags = {"fs": "y", "cr": "y", "rb": "1", "relic": "n", "7j": "y"}
        self.assertEqual(controller.flags_to_answers(flags), ["y", "y", "y", "1", "n", "y"])
        flags = {"fs": "y", "cr": "n", "rb": "n", "relic": "y", "7j": "y"}
        self.assertEqual(controller.flags_to_answers(flags), ["y", "n", "n", "y", "y"])
        self.assertEqual(controller.flags_to_answers(flags)[-1], "y")

    def test_flags_to_stdin_ends_with_newline_per_answer(self):
        flags = {"fs": "y", "cr": "n", "rb": "n", "relic": "y", "7j": "n"}
        self.assertEqual(controller.flags_to_stdin(flags), "y\nn\nn\ny\nn\n")

    def test_parse_start_args_overrides_base(self):
        base = dict(controller.DEFAULT_FLAGS)
        flags = controller.parse_start_args("fs=y rb=3", base)
        self.assertEqual(flags["fs"], "y")
        self.assertEqual(flags["rb"], "3")
        self.assertEqual(flags["cr"], "n")  # untouched, from base

    def test_parse_start_args_is_case_insensitive(self):
        base = dict(controller.DEFAULT_FLAGS)
        flags = controller.parse_start_args("FS=Y RELIC=N", base)
        self.assertEqual(flags["fs"], "y")
        self.assertEqual(flags["relic"], "n")

    def test_parse_start_args_accepts_7j(self):
        base = dict(controller.DEFAULT_FLAGS)
        flags = controller.parse_start_args("7j=y", base)
        self.assertEqual(flags["7j"], "y")
        self.assertEqual(flags["fs"], "n")

    def test_parse_start_args_rejects_unknown_flag(self):
        base = dict(controller.DEFAULT_FLAGS)
        with self.assertRaises(ValueError):
            controller.parse_start_args("bogus=y", base)

    def test_parse_start_args_rejects_malformed_token(self):
        base = dict(controller.DEFAULT_FLAGS)
        with self.assertRaises(ValueError):
            controller.parse_start_args("fs", base)

    def test_default_7j_is_n(self):
        self.assertEqual(controller.DEFAULT_FLAGS["7j"], "n")

    def test_validate_flags_accepts_defaults(self):
        controller.validate_flags(dict(controller.DEFAULT_FLAGS))  # no raise

    def test_validate_flags_accepts_7j_yes(self):
        flags = dict(controller.DEFAULT_FLAGS)
        flags["7j"] = "y"
        controller.validate_flags(flags)  # no raise

    def test_validate_flags_rejects_bad_7j(self):
        flags = dict(controller.DEFAULT_FLAGS)
        flags["7j"] = "maybe"
        with self.assertRaises(ValueError):
            controller.validate_flags(flags)

    def test_validate_flags_rejects_bad_yn(self):
        flags = dict(controller.DEFAULT_FLAGS)
        flags["fs"] = "maybe"
        with self.assertRaises(ValueError):
            controller.validate_flags(flags)

    def test_validate_flags_rejects_boost_out_of_range(self):
        flags = dict(controller.DEFAULT_FLAGS)
        flags["rb"] = "0"
        with self.assertRaises(ValueError):
            controller.validate_flags(flags)
        flags["rb"] = str(controller.BOOST_COUNT + 1)
        with self.assertRaises(ValueError):
            controller.validate_flags(flags)

    def test_validate_flags_accepts_boost_in_range(self):
        flags = dict(controller.DEFAULT_FLAGS)
        flags["rb"] = str(controller.BOOST_COUNT)
        controller.validate_flags(flags)  # no raise


class LastStartPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_log_dir = controller.LOG_DIR
        self._orig_last_start = controller.LAST_START_FILE
        controller.LOG_DIR = self._tmpdir.name
        controller.LAST_START_FILE = os.path.join(self._tmpdir.name, "last_start.json")

    def tearDown(self):
        controller.LOG_DIR = self._orig_log_dir
        controller.LAST_START_FILE = self._orig_last_start
        self._tmpdir.cleanup()

    def test_save_and_load_round_trip(self):
        flags = {"fs": "y", "cr": "n", "rb": "5", "relic": "y"}
        controller._save_last_start(flags)
        loaded = controller._load_last_start()
        self.assertEqual(loaded, flags)

    def test_load_returns_none_when_missing(self):
        self.assertIsNone(controller._load_last_start())

    def test_load_returns_none_on_corrupt_json(self):
        os.makedirs(controller.LOG_DIR, exist_ok=True)
        with open(controller.LAST_START_FILE, "w") as f:
            f.write("{not valid json")
        self.assertIsNone(controller._load_last_start())


class LogTailTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_log_dir = controller.LOG_DIR
        self._orig_log_file = controller.LOG_FILE
        self._orig_log_file_prev = controller.LOG_FILE_PREV
        controller.LOG_DIR = self._tmpdir.name
        controller.LOG_FILE = os.path.join(self._tmpdir.name, "bot.log")
        controller.LOG_FILE_PREV = os.path.join(self._tmpdir.name, "bot.log.1")

    def tearDown(self):
        controller.LOG_DIR = self._orig_log_dir
        controller.LOG_FILE = self._orig_log_file
        controller.LOG_FILE_PREV = self._orig_log_file_prev
        self._tmpdir.cleanup()

    def test_tail_log_returns_empty_when_missing(self):
        self.assertEqual(controller._tail_log(10), [])

    def test_tail_log_returns_last_n_lines(self):
        with open(controller.LOG_FILE, "w") as f:
            f.write("line1\nline2\nline3\nline4\n")
        self.assertEqual(controller._tail_log(2), ["line3", "line4"])

    def test_rotate_log_moves_existing_file(self):
        with open(controller.LOG_FILE, "w") as f:
            f.write("old run\n")
        controller._rotate_log()
        self.assertFalse(os.path.exists(controller.LOG_FILE))
        with open(controller.LOG_FILE_PREV) as f:
            self.assertEqual(f.read(), "old run\n")

    def test_rotate_log_noop_when_nothing_to_rotate(self):
        controller._rotate_log()  # should not raise
        self.assertFalse(os.path.exists(controller.LOG_FILE))


class FormatDurationTests(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(controller._format_duration(45), "45s")

    def test_minutes_and_seconds(self):
        self.assertEqual(controller._format_duration(125), "2m 5s")

    def test_hours_and_minutes(self):
        self.assertEqual(controller._format_duration(3725), "1h 2m")


class AuthorizationTests(unittest.TestCase):
    def test_is_authorized_true_for_allowed_id(self):
        c = controller.Controller()
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111, 222}):
            self.assertTrue(c.is_authorized(111))

    def test_is_authorized_false_for_unknown_id(self):
        c = controller.Controller()
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111, 222}):
            self.assertFalse(c.is_authorized(999))

    def test_is_authorized_false_when_allowlist_empty(self):
        c = controller.Controller()
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", set()):
            self.assertFalse(c.is_authorized(111))


class GetUpdatesFailureTests(unittest.TestCase):
    @patch("controller.urllib.request.urlopen")
    def test_returns_none_on_url_error(self, urlopen):
        import urllib.error

        urlopen.side_effect = urllib.error.URLError("connection reset")
        self.assertIsNone(controller.get_updates(offset=0, timeout=0))

    @patch("controller.urllib.request.urlopen")
    def test_returns_none_on_generic_exception(self, urlopen):
        urlopen.side_effect = Exception("read timed out")
        self.assertIsNone(controller.get_updates(offset=0, timeout=0))

    @patch("controller.urllib.request.urlopen")
    def test_returns_list_on_success(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({"result": [{"update_id": 1}]}).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response
        self.assertEqual(controller.get_updates(offset=0, timeout=0), [{"update_id": 1}])


class DrainBacklogTests(unittest.TestCase):
    @patch("controller.get_updates")
    def test_handles_none_without_raising(self, get_updates):
        get_updates.return_value = None
        c = controller.Controller()
        c._drain_backlog()  # should not raise
        self.assertEqual(c.offset, 0)

    @patch("controller.get_updates")
    def test_advances_offset_past_drained_updates(self, get_updates):
        get_updates.return_value = [{"update_id": 5}, {"update_id": 7}]
        c = controller.Controller()
        c._drain_backlog()
        self.assertEqual(c.offset, 8)


class BotProcessGuardTests(unittest.TestCase):
    def test_start_refuses_when_already_running(self):
        bot = controller.BotProcess()
        bot.proc = MagicMock()
        bot.proc.poll.return_value = None  # still running
        with self.assertRaises(RuntimeError):
            bot.start(dict(controller.DEFAULT_FLAGS))

    def test_is_running_false_when_no_process(self):
        bot = controller.BotProcess()
        self.assertFalse(bot.is_running())

    def test_is_running_false_after_exit(self):
        bot = controller.BotProcess()
        bot.proc = MagicMock()
        bot.proc.poll.return_value = 0  # exited
        self.assertFalse(bot.is_running())

    def test_stop_returns_false_when_not_running(self):
        bot = controller.BotProcess()
        self.assertFalse(bot.stop())

    def test_uptime_zero_before_start(self):
        bot = controller.BotProcess()
        self.assertEqual(bot.uptime(), 0)


class CmdStartAppLifecycleTests(unittest.TestCase):
    @patch("controller.device_ensure_app_running")
    @patch.object(controller, "send_message")
    def test_launches_app_before_starting_bot(self, send_message, ensure_running):
        call_order = []
        ensure_running.side_effect = lambda *a, **k: call_order.append("launch_app")
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = False
        c.bot.proc.pid = 4242
        c.bot.start.side_effect = lambda *a, **k: call_order.append("start_bot")

        controller.cmd_start(c, 123, "fs=y cr=y rb=1 relic=n")

        ensure_running.assert_called_once_with(
            controller.DEVICE_IP, controller.DEVICE_PORT, controller.GAME_PACKAGE
        )
        c.bot.start.assert_called_once()
        self.assertEqual(call_order, ["launch_app", "start_bot"])

    @patch("controller.device_ensure_app_running")
    @patch.object(controller, "send_message")
    def test_does_not_start_bot_when_app_launch_fails(self, send_message, ensure_running):
        ensure_running.side_effect = RuntimeError("app never came up")
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = False

        controller.cmd_start(c, 123, "fs=n")

        c.bot.start.assert_not_called()
        self.assertTrue(
            any("Couldn't launch" in call.args[1] for call in send_message.call_args_list)
        )

    @patch("controller.device_ensure_app_running")
    @patch.object(controller, "send_message")
    def test_skips_app_launch_when_bot_already_running(self, send_message, ensure_running):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = True

        controller.cmd_start(c, 123, "")

        ensure_running.assert_not_called()
        c.bot.start.assert_not_called()


class CmdStopKillAppLifecycleTests(unittest.TestCase):
    @patch("controller.device_force_stop_app")
    @patch.object(controller, "send_message")
    def test_stop_closes_app_after_process_exits(self, send_message, force_stop):
        c = controller.Controller()
        c.bot = MagicMock()
        # is_running: True on the initial guard check, then False once the
        # process has exited (simulates SIGINT taking effect quickly).
        c.bot.is_running.side_effect = [True, False]

        controller.cmd_stop(c, 123, "")

        c.bot.stop.assert_called_once()
        force_stop.assert_called_once_with(
            controller.DEVICE_IP, controller.DEVICE_PORT, controller.GAME_PACKAGE
        )

    @patch("controller.device_force_stop_app")
    @patch.object(controller, "send_message")
    def test_stop_noop_when_not_running(self, send_message, force_stop):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = False

        controller.cmd_stop(c, 123, "")

        c.bot.stop.assert_not_called()
        force_stop.assert_not_called()

    @patch("controller.device_force_stop_app")
    @patch.object(controller, "send_message")
    def test_kill_closes_app(self, send_message, force_stop):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.side_effect = [True, False]

        controller.cmd_kill(c, 123, "")

        c.bot.kill.assert_called_once()
        force_stop.assert_called_once_with(
            controller.DEVICE_IP, controller.DEVICE_PORT, controller.GAME_PACKAGE
        )

    @patch("controller.device_force_stop_app")
    @patch.object(controller, "send_message")
    def test_app_close_failure_reports_but_does_not_raise(self, send_message, force_stop):
        force_stop.side_effect = Exception("adb offline")
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.side_effect = [True, False]

        controller.cmd_stop(c, 123, "")  # should not raise

        self.assertTrue(
            any("couldn't close" in call.args[1].lower() for call in send_message.call_args_list)
        )

    @patch("controller.device_force_stop_app")
    @patch.object(controller, "send_message")
    def test_stop_closes_app_from_paused_state(self, send_message, force_stop):
        c = controller.Controller()
        c.paused = True

        controller.cmd_stop(c, 123, "")

        force_stop.assert_called_once_with(
            controller.DEVICE_IP, controller.DEVICE_PORT, controller.GAME_PACKAGE
        )
        self.assertFalse(c.paused)

    @patch("controller.device_force_stop_app", side_effect=RuntimeError("adb offline"))
    @patch.object(controller, "send_message")
    def test_stop_from_paused_can_retry_app_close(self, send_message, force_stop):
        c = controller.Controller()
        c.paused = True

        controller.cmd_stop(c, 123, "")

        self.assertTrue(c.paused)

    @patch("controller.device_force_stop_app")
    @patch.object(controller, "send_message")
    def test_kill_closes_app_from_paused_state(self, send_message, force_stop):
        c = controller.Controller()
        c.paused = True

        controller.cmd_kill(c, 123, "")

        force_stop.assert_called_once()
        self.assertFalse(c.paused)


class CmdPauseTests(unittest.TestCase):
    @patch("controller.device_force_stop_app")
    @patch.object(controller, "send_message")
    def test_pause_waits_for_exit_and_leaves_game_open(self, send_message, force_stop):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.side_effect = [True, False]
        c.bot.proc.poll.return_value = 0
        c.bot.proc.returncode = 0
        c.last_known_alive = True

        controller.cmd_pause(c, 123, "")

        c.bot.stop.assert_called_once()
        force_stop.assert_not_called()
        self.assertTrue(c.paused)
        self.assertFalse(c.pause_pending)
        self.assertTrue(any("PAUSED" in call.args[1] for call in send_message.call_args_list))

    @patch("controller._wait_for_bot_exit")
    @patch.object(controller, "send_message")
    def test_slow_exit_stays_pausing_until_child_exits(self, send_message, wait_for_exit):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = True
        c.bot.proc.poll.side_effect = [None, 0]
        c.bot.proc.returncode = 0
        c.last_known_alive = True

        controller.cmd_pause(c, 123, "")

        self.assertTrue(c.pause_pending)
        self.assertFalse(c.paused)
        controller.cmd_status(c, 123, "")
        self.assertIn("PAUSING", send_message.call_args.args[1])

        c._check_child_exit()

        self.assertFalse(c.pause_pending)
        self.assertTrue(c.paused)
        self.assertIn("PAUSED", send_message.call_args.args[1])
        self.assertFalse(c.last_known_alive)

    @patch.object(controller, "send_message")
    def test_start_rejected_while_pausing(self, send_message):
        c = controller.Controller()
        c.bot = MagicMock()
        c.pause_pending = True

        controller.cmd_start(c, 123, "")

        c.bot.start.assert_not_called()
        self.assertIn("still pausing", send_message.call_args.args[1])

    @patch("controller.device_ensure_app_running")
    @patch.object(controller, "send_message")
    def test_start_from_paused_reuses_last_flags(self, send_message, ensure_running):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = False
        c.bot.proc.pid = 4242
        c.paused = True
        flags = {"fs": "y", "cr": "n", "rb": "n", "relic": "y"}
        with patch.object(controller, "_load_last_start", return_value=flags):
            controller.cmd_start(c, 123, "cr=y")

        c.bot.start.assert_called_once_with(
            {"fs": "y", "cr": "y", "rb": "n", "relic": "y", "7j": "n"}
        )
        self.assertFalse(c.paused)

    @patch("controller.device_ensure_app_running", side_effect=RuntimeError("adb offline"))
    @patch.object(controller, "send_message")
    def test_failed_start_leaves_paused_state(self, send_message, ensure_running):
        c = controller.Controller()
        c.paused = True

        controller.cmd_start(c, 123, "fs=n")

        self.assertTrue(c.paused)

    @patch.object(controller, "send_message")
    def test_status_reports_paused(self, send_message):
        c = controller.Controller()
        c.paused = True

        controller.cmd_status(c, 123, "")

        self.assertIn("PAUSED", send_message.call_args.args[1])


def _callback(data, user_id=111, chat_id=5, message_id=9, callback_id="cb1"):
    return {
        "id": callback_id,
        "from": {"id": user_id},
        "data": data,
        "message": {"message_id": message_id, "chat": {"id": chat_id}},
    }


def _button_labels(markup):
    return [button["text"] for row in markup["inline_keyboard"] for button in row]


class InlineBoardTests(unittest.TestCase):
    def test_help_guide_and_usage_document_7j_and_menu(self):
        self.assertIn("7j=y|n", controller.HELP_TEXT)
        self.assertIn("7j=y|n", controller.GUIDE_TEXT)
        self.assertIn("7j=y|n", controller._START_USAGE)
        self.assertIn("/menu", controller.HELP_TEXT)

    def test_board_keyboard_labels(self):
        flags = {"fs": "y", "cr": "n", "rb": "3", "relic": "n", "7j": "y"}
        labels = _button_labels(controller.board_keyboard(flags))
        self.assertEqual(
            labels[:5],
            [
                "⚡ Fast Start",
                "🍪 Cookie Relay",
                "🏺 Relic",
                "🐇 7 Jumps",
                "🎲 Random Boost: -15% HP Drain",
            ],
        )
        # Boost picker: 11 named buttons (no numbers), exactly one marked.
        picker = labels[5:16]
        self.assertEqual(len(picker), controller.BOOST_COUNT)
        self.assertEqual(picker[2], "✅ -15% HP Drain")
        self.assertEqual(picker[0], "▫️ Double Coins")
        self.assertEqual(sum(1 for label in picker if label.startswith("✅")), 1)
        self.assertEqual(labels[16:], ["▶️ Start"])

    def test_board_button_styles_mean_highlight_is_on(self):
        flags = {"fs": "y", "cr": "n", "rb": "1", "relic": "n", "7j": "y"}
        buttons = [b for row in controller.board_keyboard(flags)["inline_keyboard"] for b in row]
        by_callback = {b["callback_data"]: b for b in buttons}
        # ON options are highlighted, OFF options stay plain.
        for key in ("fs", "7j"):
            self.assertEqual(by_callback[f"toggle:{key}"].get("style"), "primary")
        for key in ("cr", "relic"):
            self.assertNotIn("style", by_callback[f"toggle:{key}"])
        self.assertEqual(by_callback["toggle:rb"].get("style"), "primary")
        self.assertEqual(by_callback["board:start"].get("style"), "success")
        # Picker: only the selected boost is highlighted.
        self.assertEqual(by_callback["setrb:1"].get("style"), "primary")
        for i in range(2, controller.BOOST_COUNT + 1):
            self.assertNotIn("style", by_callback[f"setrb:{i}"])

    def test_board_keyboard_hides_picker_when_rb_off(self):
        flags = {"fs": "n", "cr": "n", "rb": "n", "relic": "n", "7j": "n"}
        labels = _button_labels(controller.board_keyboard(flags))
        self.assertEqual(
            labels,
            [
                "⚡ Fast Start",
                "🍪 Cookie Relay",
                "🏺 Relic",
                "🐇 7 Jumps",
                "🎲 Random Boost",
                "▶️ Start",
            ],
        )

    @patch("controller.device_ensure_app_running")
    @patch.object(controller, "send_message")
    def test_bare_start_shows_board_and_does_not_launch(self, send_message, ensure_running):
        send_message.return_value = {"ok": True, "result": {"message_id": 42}}
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = False
        c.paused = True
        saved = {"fs": "y", "cr": "n", "rb": "3", "relic": "y"}
        with patch.object(controller, "_load_last_start", return_value=saved):
            controller.cmd_start(c, 123, "")

        ensure_running.assert_not_called()
        c.bot.start.assert_not_called()
        self.assertTrue(c.paused)
        self.assertEqual(c.boards[123]["message_id"], 42)
        self.assertEqual(c.boards[123]["flags"]["fs"], "y")
        self.assertEqual(c.boards[123]["flags"]["rb"], "3")
        self.assertEqual(c.boards[123]["flags"]["7j"], "n")
        labels = _button_labels(send_message.call_args.kwargs["reply_markup"])
        self.assertIn("⚡ Fast Start", labels)
        self.assertIn("🎲 Random Boost: -15% HP Drain", labels)
        self.assertIn("▶️ Start", labels)

    @patch("controller.device_ensure_app_running")
    @patch.object(controller, "send_message")
    def test_typed_flags_still_start_immediately(self, send_message, ensure_running):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = False
        c.bot.proc.pid = 9

        controller.cmd_start(c, 123, "fs=y cr=y rb=1 relic=n 7j=y")

        ensure_running.assert_called_once()
        c.bot.start.assert_called_once_with(
            {"fs": "y", "cr": "y", "rb": "1", "relic": "n", "7j": "y"}
        )
        self.assertTrue(any(call.args[1].startswith("▶️ Started") for call in send_message.call_args_list))
        self.assertFalse(any(call.kwargs.get("reply_markup") for call in send_message.call_args_list))

    def test_dispatch_update_routes_callback_query(self):
        c = controller.Controller()
        payload = {"id": "1"}
        with patch.object(c, "_handle_callback") as handle_cb, patch.object(c, "_handle_message") as handle_msg:
            c._dispatch_update({"callback_query": payload})
        handle_cb.assert_called_once_with(payload)
        handle_msg.assert_not_called()

    def test_callback_toggle_flips_only_that_flag(self):
        c = controller.Controller()
        flags = dict(controller.DEFAULT_FLAGS)
        c.boards[5] = {"message_id": 9, "flags": flags}
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111}), \
             patch.object(controller, "answer_callback_query") as answer, \
             patch.object(controller, "edit_message_reply_markup") as edit:
            c._handle_callback(_callback("toggle:cr"))

        self.assertEqual(flags["cr"], "y")
        self.assertEqual(flags["fs"], "n")
        self.assertEqual(flags["7j"], "n")
        answer.assert_called_once_with("cb1")
        self.assertEqual(edit.call_args.args[0], 5)
        self.assertEqual(edit.call_args.args[1], 9)
        buttons = [b for row in edit.call_args.args[2]["inline_keyboard"] for b in row]
        by_callback = {b["callback_data"]: b for b in buttons}
        self.assertEqual(by_callback["toggle:cr"].get("style"), "primary")
        self.assertNotIn("style", by_callback["toggle:fs"])
        self.assertNotIn("style", by_callback["toggle:7j"])

    def test_rb_toggle_on_shows_picker_then_off_hides_it(self):
        c = controller.Controller()
        flags = dict(controller.DEFAULT_FLAGS)
        c.boards[5] = {"message_id": 9, "flags": flags}
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111}), \
             patch.object(controller, "answer_callback_query"), \
             patch.object(controller, "edit_message_reply_markup") as edit:
            c._handle_callback(_callback("toggle:rb"))
            self.assertEqual(flags["rb"], "1")
            labels = _button_labels(edit.call_args.args[2])
            self.assertIn("🎲 Random Boost: Double Coins", labels)
            self.assertIn("✅ Double Coins", labels)
            c._handle_callback(_callback("toggle:rb", callback_id="cb2"))
            self.assertEqual(flags["rb"], "n")
            labels = _button_labels(edit.call_args.args[2])
            self.assertIn("🎲 Random Boost", labels)
            self.assertNotIn("✅ Double Coins", labels)
            self.assertNotIn("▫️ +15% Score Bonus", labels)

    def test_setrb_selects_exactly_one_boost(self):
        c = controller.Controller()
        flags = dict(controller.DEFAULT_FLAGS)
        flags["rb"] = "1"
        c.boards[5] = {"message_id": 9, "flags": flags}
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111}), \
             patch.object(controller, "answer_callback_query"), \
             patch.object(controller, "edit_message_reply_markup") as edit:
            c._handle_callback(_callback("setrb:5"))
            self.assertEqual(flags["rb"], "5")
            labels = _button_labels(edit.call_args.args[2])
            self.assertIn("✅ 70% Crush Chance", labels)
            self.assertNotIn("✅ Double Coins", labels)
            c._handle_callback(_callback("setrb:99", callback_id="cb2"))
            self.assertEqual(flags["rb"], "5")

    def test_unauthorized_callback_is_ignored(self):
        c = controller.Controller()
        flags = dict(controller.DEFAULT_FLAGS)
        c.boards[5] = {"message_id": 9, "flags": flags}
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111}), \
             patch.object(controller, "answer_callback_query") as answer, \
             patch.object(controller, "edit_message_reply_markup") as edit, \
             patch.object(controller, "send_message") as send_message:
            c._handle_callback(_callback("toggle:fs", user_id=999))
            c._handle_callback(_callback("menu:status", user_id=999))
            c._handle_callback(_callback("board:start", user_id=999))

        answer.assert_not_called()
        edit.assert_not_called()
        send_message.assert_not_called()
        self.assertEqual(flags["fs"], "n")

    @patch("controller.device_ensure_app_running")
    @patch.object(controller, "answer_callback_query")
    @patch.object(controller, "send_message")
    def test_start_button_launches_with_board_flags(self, send_message, answer, ensure_running):
        c = controller.Controller()
        c.bot = MagicMock()
        c.bot.is_running.return_value = False
        c.bot.proc.pid = 7
        flags = {"fs": "y", "cr": "n", "rb": "2", "relic": "y", "7j": "y"}
        c.boards[5] = {"message_id": 9, "flags": dict(flags)}
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111}):
            c._handle_callback(_callback("board:start"))

        ensure_running.assert_called_once()
        c.bot.start.assert_called_once_with(flags)
        answer.assert_called_once_with("cb1")
        self.assertTrue(any(call.args[1].startswith("▶️ Started") for call in send_message.call_args_list))

    @patch.object(controller, "send_message")
    def test_menu_command_sends_command_buttons(self, send_message):
        controller.cmd_menu(controller.Controller(), 123, "")
        labels = _button_labels(send_message.call_args.kwargs["reply_markup"])
        self.assertEqual(labels, ["📊 Status", "📋 Logs", "📸 Screenshot", "⏸️ Pause", "🛑 Stop"])

    @patch("controller.device_force_stop_app")
    @patch("controller.capture_screenshot_bytes", return_value=b"png")
    @patch("controller.send_photo")
    @patch("controller._tail_log", return_value=["hello"])
    @patch.object(controller, "send_message")
    @patch.object(controller, "answer_callback_query")
    def test_menu_callbacks_run_existing_commands(
        self, answer, send_message, tail, send_photo, capture, force_stop
    ):
        c = controller.Controller()
        with patch.object(controller, "TELEGRAM_ALLOWED_USER_IDS", {111}):
            c._handle_callback(_callback("menu:status"))
            self.assertTrue(any("STOPPED" in call.args[1] for call in send_message.call_args_list))

            send_message.reset_mock()
            c._handle_callback(_callback("menu:logs", callback_id="logs"))
            tail.assert_called_with(30)
            self.assertTrue(any("hello" in call.args[1] for call in send_message.call_args_list))

            c._handle_callback(_callback("menu:screenshot", callback_id="shot"))
            capture.assert_called_once()
            send_photo.assert_called_once()

            send_message.reset_mock()
            c._handle_callback(_callback("menu:pause", callback_id="pause"))
            self.assertTrue(any("not running" in call.args[1] for call in send_message.call_args_list))

            send_message.reset_mock()
            c._handle_callback(_callback("menu:stop", callback_id="stop"))
            self.assertTrue(any("not running" in call.args[1] for call in send_message.call_args_list))

        force_stop.assert_not_called()
        self.assertEqual(answer.call_count, 5)


if __name__ == "__main__":
    unittest.main()
