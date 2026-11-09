import unittest
from unittest.mock import MagicMock, patch

import adb


class DeviceForceStopAppTests(unittest.TestCase):
    @patch("adb.subprocess.run")
    def test_runs_force_stop_command(self, run):
        adb.device_force_stop_app("127.0.0.1", 5555, "com.devsisters.crg")
        args = run.call_args[0][0]
        self.assertEqual(
            args,
            ["adb", "-s", "127.0.0.1:5555", "shell", "cmd", "activity", "force-stop", "com.devsisters.crg"],
        )


class DeviceLaunchAppTests(unittest.TestCase):
    @patch("adb.subprocess.run")
    def test_runs_monkey_command(self, run):
        adb.device_launch_app("127.0.0.1", 5555, "com.devsisters.crg")
        args = run.call_args[0][0]
        self.assertEqual(
            args,
            ["adb", "-s", "127.0.0.1:5555", "shell", "monkey", "-p", "com.devsisters.crg", "1"],
        )


@patch("adb.device_connect")
class DeviceEnsureAppRunningTests(unittest.TestCase):
    @patch("adb.device_launch_app")
    @patch("adb.device_is_app_running")
    def test_noop_when_already_running(self, is_running, launch_app, _connect):
        is_running.return_value = True
        adb.device_ensure_app_running("127.0.0.1", 5555, "pkg")
        launch_app.assert_not_called()

    @patch("adb.time.sleep")
    @patch("adb.device_launch_app")
    @patch("adb.device_is_app_running")
    def test_launches_and_waits_when_not_running(self, is_running, launch_app, _sleep, _connect):
        # not running -> launch -> now running
        is_running.side_effect = [False, True]
        adb.device_ensure_app_running("127.0.0.1", 5555, "pkg", launch_wait=1, max_wait=5, poll_interval=1)
        launch_app.assert_called_once_with("127.0.0.1", 5555, "pkg")

    @patch("adb.time.time")
    @patch("adb.time.sleep")
    @patch("adb.device_launch_app")
    @patch("adb.device_is_app_running")
    def test_raises_after_timeout(self, is_running, launch_app, _sleep, mock_time, _connect):
        is_running.return_value = False
        # Simulate the clock advancing past the deadline immediately.
        mock_time.side_effect = [0, 100, 100]
        with self.assertRaises(RuntimeError):
            adb.device_ensure_app_running("127.0.0.1", 5555, "pkg", launch_wait=1, max_wait=5, poll_interval=1)


if __name__ == "__main__":
    unittest.main()
