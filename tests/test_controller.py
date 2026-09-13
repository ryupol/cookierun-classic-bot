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
        flags = {"fs": "y", "cr": "y", "rb": "1", "relic": "n"}
        self.assertEqual(controller.flags_to_answers(flags), ["y", "y", "y", "1", "n"])

    def test_flags_to_answers_without_boost(self):
        flags = {"fs": "n", "cr": "n", "rb": "n", "relic": "n"}
        self.assertEqual(controller.flags_to_answers(flags), ["n", "n", "n", "n"])

    def test_flags_to_stdin_ends_with_newline_per_answer(self):
        flags = {"fs": "y", "cr": "n", "rb": "n", "relic": "y"}
        self.assertEqual(controller.flags_to_stdin(flags), "y\nn\nn\ny\n")

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

    def test_parse_start_args_rejects_unknown_flag(self):
        base = dict(controller.DEFAULT_FLAGS)
        with self.assertRaises(ValueError):
            controller.parse_start_args("bogus=y", base)

    def test_parse_start_args_rejects_malformed_token(self):
        base = dict(controller.DEFAULT_FLAGS)
        with self.assertRaises(ValueError):
            controller.parse_start_args("fs", base)

    def test_validate_flags_accepts_defaults(self):
        controller.validate_flags(dict(controller.DEFAULT_FLAGS))  # no raise

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

        controller.cmd_start(c, 123, "")

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


if __name__ == "__main__":
    unittest.main()
