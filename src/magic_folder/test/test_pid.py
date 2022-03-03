# Copyright 2022 Least Authority TFA GmbH
# See COPYING for details.

from twisted.logger import (
    Logger,
)
from twisted.python.filepath import (
    FilePath,
)
from testtools.matchers import (
    Equals,
    Contains,
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
