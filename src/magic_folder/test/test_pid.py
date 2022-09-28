# Copyright 2022 Least Authority TFA GmbH
# See COPYING for details.

import re
import sys
from subprocess import (
    Popen,
    PIPE,
)

from twisted.logger import (
    Logger,
)
from twisted.python.filepath import (
    FilePath,
)
from testtools.matchers import (
    Always,
    Equals,
    Contains,
    ContainsDict,
    AllMatch,
    HasLength,
)
from twisted.internet.testing import (
    EventLoggingObserver,
)
from hypothesis import (
    given,
    assume,
)
from hypothesis.strategies import (
    text,
)

from .common import (
    SyncTestCase,
)
from ..pid import (
    check_pid_process,
    InvalidPidFile,
    ProcessInTheWay,
    _pidfile_to_lockpath,
)


class _FakeProcess:
    """
    Enough of psutil.Process to test check_pid_process
    """
    running = True

    def __init__(self, pid):
        self.pid = pid

    def create_time(self):
        return 123.4

    def terminate(self):
        self.running = False


class TestPidObserver(SyncTestCase):
    """
    Confirm operation of magic_folder.pid functions
    """

    def test_happy(self):
        """
        normal operation of pid-file writing
        """
        pidfile = FilePath(self.mktemp())
        log = Logger()
        with check_pid_process(pidfile, log, find_process=_FakeProcess):
            self.assertThat(
                pidfile.exists(),
                Equals(True),
            )
        self.assertThat(
            pidfile.exists(),
            Equals(False),
        )

    def test_not_running(self):
        """
        a pid-file refers to a non-running process
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"65537 1234.5")  # "impossible" process-id .. right?
        obs = EventLoggingObserver()
        log = Logger()
        log.observer = obs
        with check_pid_process(pidfile, log):
            pass

        events = list(obs)

        # both logged events should have a "pidpath" kwarg
        self.assertThat(events, HasLength(2))
        self.assertThat(
            events,
            AllMatch(
                ContainsDict({
                    "pidpath": Always(),
                }),
            )
        )

    def test_existing(self):
        """
        a pid-file refers to a running process so we should exit
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"0 0.0\n")
        obs = EventLoggingObserver()
        log = Logger()
        log.observer = obs

        with self.assertRaises(Exception) as ctx:
            with check_pid_process(pidfile, log, find_process=_FakeProcess):
                pass
        self.assertThat(
            str(ctx.exception),
            Contains("already running")
        )

    good_file_content_re = re.compile(r"\w[0-9]*\w[0-9]*\w")

    @given(text())
    def test_invalid_pidfile(self, bad_content):
        """
        an invalid PID file produces and error
        """
        assume(not self.good_file_content_re.match(bad_content))
        bad_content = b"not pids"
        pidfile = FilePath("pidfile")
        pidfile.setContent(bad_content)

        with self.assertRaises(InvalidPidFile):
            with check_pid_process(pidfile, Logger()):
                pass


class PidFileLocking(SyncTestCase):
    """
    The pidfile locking implementation prevents multiple processes
    from opening the file at once
    """

    def test_locking(self):
        """
        Fail to create a pidfile if another process has the lock already.
        """
        # this can't just be "our" process because the locking library
        # allows the same process to acquire a lock multiple times.
        pidfile = FilePath(self.mktemp())
        lockfile = _pidfile_to_lockpath(pidfile)

        with open("other_lock.py", "w") as f:
            f.write(
                "\n".join([
                    "import filelock, time, sys",
                    "with filelock.FileLock(sys.argv[1], timeout=1):",
                    "    sys.stdout.write('.\\n')",
                    "    sys.stdout.flush()",
                    "    time.sleep(10)",
                ])
            )
        proc = Popen(
            [sys.executable, "other_lock.py", lockfile.path],
            stdout=PIPE,
            stderr=PIPE,
        )
        # make sure our subprocess has had time to acquire the lock
        # for sure (from the "." it prints)
        proc.stdout.read(2)

        # acquiring the same lock should fail; it is locked by the subprocess
        with self.assertRaises(ProcessInTheWay):
            with check_pid_process(pidfile, Logger()):
                pass
        proc.terminate()
