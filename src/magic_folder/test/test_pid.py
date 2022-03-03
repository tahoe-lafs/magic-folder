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
        with check_pid_process(pidfile, log):
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
        an existing pid-file is discovered
        """
        pidfile = FilePath(self.mktemp())
        log = Logger()
        with check_pid_process(pidfile, log):
            with self.assertRaises(Exception) as ctx:
                with check_pid_process(pidfile, log):
                    pass
            self.assertThat(
                str(ctx.exception),
                Contains("existing magic-folder process")
            )

    def test_not_running(self):
        """
        a pid-file refers to a non-running process
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"0")
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
