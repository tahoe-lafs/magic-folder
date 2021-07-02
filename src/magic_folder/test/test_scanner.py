from __future__ import absolute_import, division, print_function, unicode_literals

"""
Tests relating generally to magic_folder.scanner
"""

from hypothesis import given
from testtools.matchers import Always, Equals
from testtools.twistedsupport import succeeded
from twisted.internet.defer import succeed
from twisted.internet.task import Cooperator
from twisted.python.filepath import FilePath

from ..config import create_testing_configuration
from ..magicpath import path2magic
from ..scanner import ScannerService, find_updated_files
from ..snapshot import RemoteSnapshot, create_local_author
from ..util.file import PathState, get_pathinfo
from .common import SyncTestCase
from .strategies import relative_paths


# This is a path state that doesn't correspond to a file written during the test.
OLD_PATH_STATE = PathState(0, 0, 0)


class FindUpdatesTests(SyncTestCase):
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
            None,
        )
        # Use a cooperator that does not cooperate.
        self.cooperator = Cooperator(
            terminationPredicateFactory=lambda: lambda: False,
            scheduler=lambda f: f(),
        )
        self.addCleanup(self.cooperator.stop)

    def setup_example(self):
        self.magic_path.remove()
        self.magic_path.makedirs()

    @given(
        relative_paths(),
    )
    def test_scan_new(self, name):
        """
        A completely new file is scanned
        """
        local = self.magic_path.preauthChild(name)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")

        files = []
        self.assertThat(
            find_updated_files(self.cooperator, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([local]),
        )

    @given(
        relative_paths(),
    )
    def test_scan_nothing(self, name):
        """
        An existing, non-updated file is not scanned
        """
        local = self.magic_path.preauthChild(name)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")
        snap = RemoteSnapshot(
            "new-file",
            self.author,
            metadata={
                "modification_time": int(
                    local.asBytesMode("utf-8").getModificationTime()
                ),
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[],
            content_cap="URI:CHK:",
        )
        self.config.store_downloaded_snapshot(
            path2magic(name), snap, get_pathinfo(local).state
        )

        files = []
        self.assertThat(
            find_updated_files(self.cooperator, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(files, Equals([]))

    @given(
        relative_paths(),
    )
    def test_scan_something_remote(self, name):
        """
        We scan an update to a file we already know about.
        """
        local = self.magic_path.preauthChild(name)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")
        snap = RemoteSnapshot(
            name,
            self.author,
            metadata={
                # this remote is 2min older than our local file
                "modification_time": int(
                    local.asBytesMode("utf-8").getModificationTime()
                )
                - 120,
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[],
            content_cap="URI:CHK:",
        )
        self.config.store_downloaded_snapshot(
            path2magic(name),
            snap,
            OLD_PATH_STATE,
        )

        files = []
        self.assertThat(
            find_updated_files(self.cooperator, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(files, Equals([local]))

    @given(
        relative_paths(),
    )
    def test_scan_something_local(self, name):
        """
        We scan an update to a file we already know about (but only locally).
        """
        local = self.magic_path.preauthChild("existing-file")
        local.asBytesMode("utf-8").setContent(b"dummy\n")
        stash_dir = FilePath(self.mktemp())
        stash_dir.makedirs()

        self.config.store_currentsnapshot_state("existing-file", OLD_PATH_STATE)

        files = []
        self.assertThat(
            find_updated_files(self.cooperator, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(files, Equals([local]))

    @given(
        relative_paths(),
    )
    def test_scan_service(self, name):
        """
        The scanner service iteself discovers a_file
        """
        name = "a_file"
        local = self.magic_path.preauthChild(name)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")

        files = []

        class SnapshotService(object):
            def add_file(self, f):
                files.append(f)
                return succeed(None)

        service = ScannerService(
            self.config,
            SnapshotService(),
            object(),
            cooperator=self.cooperator,
        )
        service.startService()
        self.addCleanup(service.stopService)

        self.assertThat(
            service.scan_once(),
            succeeded(Always()),
        )

        self.assertThat(files, Equals([local]))
