# Copyright 2022 Least Authority TFA GmbH
# See COPYING for details.

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
    MatchesListwise,
    AfterPreprocessing,
    AllMatch,
    HasLength,
)
from twisted.internet.testing import (
    EventLoggingObserver,
)

from .common import (
    SyncTestCase,
)
from ..pid import (
    check_pid_process,
)


class _FakeProcess:
    """
    Enough of psutil.Process to test check_pid_process
    """
    running = True

    def __init__(self, pid):
        self.pid = pid

    def cmdline(self):
        return ["magic-folder"]

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

    def test_existing(self):
        """
        an existing pid-file is discovered and killed
        """
        pidfile = FilePath(self.mktemp())
        log = Logger()
        procs = []

        def create_process(pid):
            procs.append(_FakeProcess(pid))
            return procs[-1]

        with check_pid_process(pidfile, log, find_process=create_process):
            with check_pid_process(pidfile, log, find_process=create_process):
                pass

        self.assertThat(
            procs,
            MatchesListwise([
                AfterPreprocessing(
                    lambda x: x.running,
                    Equals(False)
                ),
            ])
        )

    def test_not_running(self):
        """
        a pid-file refers to a non-running process
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"65537")  # "impossible" process-id .. right?
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

    def test_kill(self):
        """
        a pid-file refers to a magic-folder process so it should be killed
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"0")
        obs = EventLoggingObserver()
        log = Logger()
        log.observer = obs

        with check_pid_process(pidfile, log, find_process=_FakeProcess):
            pass

        events = list(obs)

        # both logged events should have a "pidpath" kwarg
        self.assertThat(events, HasLength(2))
        self.assertThat(
            events,
            MatchesListwise([
                ContainsDict({
                    "pid": Equals(0),
                }),
                ContainsDict({
                    "pidpath": Always(),
                }),
            ])
        )

    def test_kill_wrong_process(self):
        """
        a pid-file refers to a non-magic-folder process so it should not
        be killed
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"0")
        obs = EventLoggingObserver()
        log = Logger()
        log.observer = obs

        class _FakeNonMagicProcess(_FakeProcess):
            def cmdline(self):
                return ["init"]

        with self.assertRaises(Exception) as ctx:
            with check_pid_process(pidfile, log, find_process=_FakeNonMagicProcess):
                pass

        self.assertThat(
            str(ctx.exception),
            Contains("not killing"),
        )
