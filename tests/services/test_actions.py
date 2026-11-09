import unittest
from unittest.mock import patch

import services.actions as actions
import config


class RecoverFriendOverlayTests(unittest.TestCase):
    @patch("services.actions.time.sleep")
    @patch("services.actions.safe_device_tap")
    @patch("services.actions.detect_templates")
    def test_closes_friend_cookie_before_friend_info(self, detect_templates, tap, _sleep):
        detect_templates.side_effect = lambda _screen, templates, _region: (
            [(500, 25, 285, 65)]
            if templates == actions.FRIEND_COOKIE_DIALOG_TEMPLATE
            else []
        )

        recovered = actions.recover_friend_overlay(object())

        self.assertTrue(recovered)
        tap.assert_called_once_with(
            actions.DEVICE_IP,
            actions.DEVICE_PORT,
            actions.FRIEND_COOKIE_DIALOG_CLOSE_BUTTON[0],
            actions.FRIEND_COOKIE_DIALOG_CLOSE_BUTTON[1],
        )

    @patch("services.actions.time.sleep")
    @patch("services.actions.safe_device_tap")
    @patch("services.actions.detect_templates")
    def test_cancels_leftover_send_confirmation(self, detect_templates, tap, _sleep):
        detect_templates.side_effect = lambda _screen, templates, region: (
            [(505, 305, 275, 50)]
            if templates == actions.SEND_LIFE_CONFIRM_DIALOG_TEMPLATE
            and region == actions.SEND_LIFE_CONFIRM_DIALOG_REGION
            else []
        )

        recovered = actions.recover_friend_overlay(object())

        self.assertTrue(recovered)
        tap.assert_called_once_with(
            actions.DEVICE_IP,
            actions.DEVICE_PORT,
            actions.CANCEL_SEND_LIFE_BUTTON[0],
            actions.CANCEL_SEND_LIFE_BUTTON[1],
        )


class OpeningJumpsTests(unittest.TestCase):
    @patch("services.actions.time.sleep")
    @patch("services.actions.safe_device_tap")
    def test_taps_jump_button_seven_times(self, tap, sleep):
        actions.do_opening_jumps()

        self.assertEqual(tap.call_count, 7)
        for tapped in tap.call_args_list:
            self.assertEqual(
                tapped.args,
                (actions.DEVICE_IP, actions.DEVICE_PORT, actions.JUMP_BUTTON[0], actions.JUMP_BUTTON[1]),
            )
        self.assertEqual(sleep.call_count, 6)
        for slept in sleep.call_args_list:
            self.assertTrue(0.5 <= slept.args[0] <= 0.7)


class QuickReceiveAndSendLivesTests(unittest.TestCase):
    def _points(self, tap):
        return [(c.args[2], c.args[3]) for c in tap.call_args_list]

    @patch("services.actions.time.sleep")
    @patch("services.actions.safe_device_tap")
    @patch("services.actions.device_capture_screen", return_value=object())
    @patch("services.actions.detect_templates")
    def test_no_lives_closes_mail_without_quick_send(self, detect_templates, _capture, tap, _sleep):
        detect_templates.side_effect = (
            lambda _screen, templates, _region: templates == actions.NO_LIVES_TO_RECEIVE_TEMPLATE
        )

        actions.handle_quick_receive_and_send_lives()

        points = self._points(tap)
        self.assertEqual(
            points,
            [
                actions.MAIL_BOX_BUTTON,
                actions.MAIL_BOX_LIVES_TAB_BUTTON,
                actions.MAIL_BOX_CLOSE_BUTTON,
            ],
        )
        self.assertNotIn(config.CONFIRM_SEND_LIFE_BUTTON, points)

    @patch("services.actions.time.sleep")
    @patch("services.actions.safe_device_tap")
    @patch("services.actions.device_capture_screen", return_value=object())
    @patch("services.actions.detect_templates")
    def test_lives_present_clicks_once_then_confirms_and_closes(self, detect_templates, _capture, tap, _sleep):
        def detect(_screen, templates, _region):
            if templates == actions.NO_LIVES_TO_RECEIVE_TEMPLATE:
                return []
            if templates == actions.ALL_LIVES_RECEIVED_AND_SENT_TEMPLATE:
                return [(1, 2, 3, 4)]
            if templates == config.CONFIRM_SEND_LIFE_TEMPLATE:
                return [(10, 10, 10, 10)]
            return []

        detect_templates.side_effect = detect

        actions.handle_quick_receive_and_send_lives()

        points = self._points(tap)
        self.assertEqual(
            points,
            [
                actions.MAIL_BOX_BUTTON,
                actions.MAIL_BOX_LIVES_TAB_BUTTON,
                actions.QUICK_RECEIVE_AND_SEND_LIVES_BUTTON,
                actions.ACCEPT_ALL_LIVES_RECEIVED_AND_SENT_BUTTON,
                actions.MAIL_BOX_CLOSE_BUTTON,
            ],
        )
        self.assertEqual(points.count(actions.QUICK_RECEIVE_AND_SEND_LIVES_BUTTON), 1)
        self.assertNotIn(config.CONFIRM_SEND_LIFE_BUTTON, points)


if __name__ == "__main__":
    unittest.main()
