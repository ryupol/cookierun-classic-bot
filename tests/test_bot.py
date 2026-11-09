import inspect
import unittest
from unittest.mock import MagicMock, patch

import bot
import config


class UnknownStageWatchdogTests(unittest.TestCase):
    def test_unknown_stage_times_out_at_group_limit(self):
        timeout = bot.UNKNOWN_STAGE_RESET_TIMEOUT["PRE_GAME"]

        self.assertFalse(bot.unknown_stage_timed_out(100.0, "PRE_GAME", 100.0 + timeout - 0.1))
        self.assertTrue(bot.unknown_stage_timed_out(100.0, "PRE_GAME", 100.0 + timeout))

    def test_known_stage_has_no_watchdog_start(self):
        self.assertFalse(bot.unknown_stage_timed_out(None, "PRE_GAME", 999.0))


class StartupDetectionTests(unittest.TestCase):
    @patch("bot.time.sleep")
    @patch("bot.recover_friend_overlay", return_value=False)
    @patch("bot.detect_stage", side_effect=[None, KeyboardInterrupt])
    @patch("bot.device_capture_screen")
    @patch("bot.prompt_user_options", return_value={"detect_relic": True})
    @patch("bot.load_templates")
    @patch("bot.device_connect")
    def test_fresh_process_scans_all_stages_once(
        self, connect, load_templates, options, capture, detect, recover, sleep
    ):
        bot.main()

        self.assertNotIn("stage_names", detect.call_args_list[0].kwargs)
        self.assertEqual(detect.call_args_list[0].kwargs, {"exclude": None})
        self.assertEqual(
            detect.call_args_list[1].args[1],
            bot.get_detection_stage_names("PRE_GAME"),
        )


class OpeningJumpsOptionTests(unittest.TestCase):
    def _options(self, **overrides):
        options = {
            "use_fast_start": False,
            "use_cookie_relay": False,
            "use_desired_random_boost": False,
            "desired_boost_template": None,
            "desired_boost_name": None,
            "detect_relic": False,
            "use_opening_jumps": False,
        }
        options.update(overrides)
        return options

    def _run_game_start(self, options, jumps, fast):
        with patch("bot.time.sleep"), \
             patch("bot.recover_friend_overlay", return_value=False), \
             patch("bot.detect_stage", side_effect=["GAME_START", KeyboardInterrupt]), \
             patch("bot.device_capture_screen"), \
             patch("bot.prompt_user_options", return_value=options), \
             patch("bot.load_templates"), \
             patch("bot.device_connect"), \
             patch("bot.do_opening_jumps", jumps), \
             patch("bot.using_fast_start", fast):
            bot.main()

    def test_game_start_calls_opening_jumps_once_when_enabled(self):
        jumps = MagicMock()
        fast = MagicMock()
        order = []
        fast.side_effect = lambda: order.append("fast")
        jumps.side_effect = lambda *args, **kwargs: order.append("jumps")

        self._run_game_start(
            self._options(use_fast_start=True, use_opening_jumps=True),
            jumps,
            fast,
        )

        jumps.assert_called_once_with()
        self.assertEqual(order, ["fast", "jumps"])

    def test_game_start_skips_opening_jumps_when_disabled(self):
        jumps = MagicMock()
        fast = MagicMock()

        self._run_game_start(self._options(use_opening_jumps=False), jumps, fast)

        jumps.assert_not_called()
        fast.assert_not_called()

    @patch("builtins.input", side_effect=["n", "n", "n", "y", "y"])
    def test_prompt_user_options_returns_opening_jumps(self, _input):
        options = bot.prompt_user_options()

        self.assertFalse(options["use_fast_start"])
        self.assertFalse(options["use_cookie_relay"])
        self.assertTrue(options["detect_relic"])
        self.assertTrue(options["use_opening_jumps"])

    @patch("builtins.input", side_effect=["y", "n", "n", "n", "n"])
    def test_prompt_user_options_opening_jumps_can_be_off(self, _input):
        options = bot.prompt_user_options()

        self.assertTrue(options["use_fast_start"])
        self.assertFalse(options["use_opening_jumps"])


class SessionResetLivesTests(unittest.TestCase):
    def test_source_has_no_pending_send_friend_life(self):
        source = inspect.getsource(bot)
        self.assertNotIn("pending_send_friend_life", source)
        self.assertNotIn("handle_send_friend_life", source)

    @patch("bot.random.uniform", return_value=0)
    @patch("bot.time.sleep")
    @patch("bot.close_announcement_dialog")
    @patch("bot.start_game")
    @patch("bot.device_reset_app")
    @patch("bot.handle_quick_receive_and_send_lives")
    @patch("bot.recover_friend_overlay", return_value=False)
    @patch("bot.detect_stage", side_effect=["MAINMENU", KeyboardInterrupt])
    @patch("bot.device_capture_screen")
    @patch("bot.prompt_user_options", return_value={
        "detect_relic": False,
        "use_fast_start": False,
        "use_cookie_relay": False,
        "use_desired_random_boost": False,
        "use_opening_jumps": False,
    })
    @patch("bot.load_templates")
    @patch("bot.device_connect")
    def test_session_reset_does_not_call_a_send_life_handler(
        self, _connect, _load, _options, _capture, _detect, _recover, quick, reset, _start, _close, _sleep, _uniform
    ):
        bot.main()

        reset.assert_called()
        quick.assert_not_called()

    @patch("bot.random.uniform", side_effect=lambda low, high: 10**9 if (low, high) == bot.SESSION_RESET_INTERVAL else 0)
    @patch("bot.time.sleep")
    @patch("bot.close_announcement_dialog")
    @patch("bot.start_game")
    @patch("bot.device_reset_app")
    @patch("bot.handle_quick_receive_and_send_lives")
    @patch("bot.recover_friend_overlay", return_value=False)
    @patch("bot.detect_stage", side_effect=["MAINMENU", KeyboardInterrupt])
    @patch("bot.device_capture_screen")
    @patch("bot.prompt_user_options", return_value={
        "detect_relic": False,
        "use_fast_start": False,
        "use_cookie_relay": False,
        "use_desired_random_boost": False,
        "use_opening_jumps": False,
    })
    @patch("bot.load_templates")
    @patch("bot.device_connect")
    def test_lives_interval_still_runs_mailbox_flow(
        self, _connect, _load, _options, _capture, _detect, _recover, quick, reset, start, _close, _sleep, _uniform
    ):
        bot.main()

        quick.assert_called_once_with()
        reset.assert_not_called()
        start.assert_not_called()


class LivesIntervalTests(unittest.TestCase):
    def test_lives_interval_is_three_to_six_hours(self):
        self.assertEqual(config.LIVES_INTERVAL, (10800, 21600))
        self.assertEqual(bot.LIVES_INTERVAL, (10800, 21600))


if __name__ == "__main__":
    unittest.main()
