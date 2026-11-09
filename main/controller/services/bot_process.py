"""Bot child-process lifecycle: spawn main.py with the start flags piped to
its stdin, signal the whole process group on stop/kill, report uptime."""

import os
import signal
import subprocess
import sys
import time

from ..models.flags import flags_to_stdin
from ..repositories.last_start import _save_last_start
from ..repositories.logs import LOG_FILE, _rotate_log


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
                [sys.executable, "-u", "-m", "bot"],
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
