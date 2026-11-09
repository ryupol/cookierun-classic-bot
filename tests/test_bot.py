import unittest
from unittest.mock import patch

import bot


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


if __name__ == "__main__":
    unittest.main()
