from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Tests relating generally to magic_folder.scanner
"""

from eliot.twisted import (
    inline_callbacks,
)

from testtools.matchers import (
    Always,
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)
from twisted.internet import reactor
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    succeed,
)

from ..config import (
    create_testing_configuration,
)
from ..snapshot import (
    create_local_author,
    RemoteSnapshot,
    create_snapshot,
)

from .common import (
    AsyncTestCase,
)
from ..scanner import (
    find_updated_files,
    ScannerService,
)


class FindUpdatesTests(AsyncTestCase):
    """
    Tests for ``find_updated_files``
    """

    def setUp(self):
        super(FindUpdatesTests, self).setUp()
        self.author = create_local_author("alice")
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self._global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath("dummy"),
        )
        self.collective_cap = "URI:DIR2:mfqwcylbmfqwcylbmfqwcylbme:mfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqq"
        self.personal_cap = "URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra"

        self.config = self._global_config.create_magic_folder(
            "default",
            self.magic_path,
            self.author,
            self.collective_cap,
            self.personal_cap,
            1,
            1,
        )

    def test_scan_new(self):
        """
        A completely new file is scanned
        """
        self.magic_path.child("new-file").setContent(b"dummy\n")

        files = []
        self.assertThat(
            find_updated_files(reactor, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([self.magic_path.child("new-file")]),
        )

    def test_scan_nothing(self):
        """
        An existing, non-updated file is not scanned
        """
        local = self.magic_path.child("new-file")
        local.setContent(b"dummy\n")
        snap = RemoteSnapshot(
            "new-file",
            self.author,
            metadata={
                "modification_time": int(local.getModificationTime()),
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[],
            content_cap="URI:CHK:",
        )
        self.config.store_remotesnapshot("new-file", snap)

        files = []
        self.assertThat(
            find_updated_files(reactor, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([])
        )

    def test_scan_something_remote(self):
        """
        We scan an update to a file we already know about.
        """
        local = self.magic_path.child("existing-file")
        local.setContent(b"dummy\n")
        snap = RemoteSnapshot(
            "existing-file",
            self.author,
            metadata={
                # this remote is 2min older than our local file
                "modification_time": int(local.getModificationTime()) - 120,
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[],
            content_cap="URI:CHK:",
        )
        self.config.store_remotesnapshot("existing-file", snap)

        files = []
        self.assertThat(
            find_updated_files(reactor, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([local])
        )

    @inline_callbacks
    def test_scan_something_local(self):
        """
        We scan an update to a file we already know about (but only locally).
        """
        local = self.magic_path.child("existing-file")
        local.setContent(b"dummy\n")
        stash_dir = FilePath(self.mktemp())
        stash_dir.makedirs()

        snap = yield create_snapshot(
            "existing-file",
            self.author,
            local.open("r"),
            stash_dir,
            modified_time=local.getModificationTime() - 120,
        )
        self.config.store_local_snapshot(snap)

        files = []
        self.assertThat(
            find_updated_files(reactor, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([local])
        )

    @inline_callbacks
    def test_scan_service(self):
        """
        The scanner service iteself discovers a_file
        """
        local = self.magic_path.child("a_file")
        local.setContent(b"dummy\n")

        files = []

        class SnapshotService(object):
            def add_file(self, f):
                files.append(f)
                return succeed(None)

        service = ScannerService(
            reactor,
            self.config,
            SnapshotService(),
            object(),
        )
        service.startService()
        yield service.stopService()
