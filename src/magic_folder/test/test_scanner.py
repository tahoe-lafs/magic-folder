from __future__ import absolute_import, division, print_function, unicode_literals

"""
Tests relating generally to magic_folder.scanner
"""

from hypothesis import given
from testtools.matchers import (
    Always,
    Equals,
    MatchesListwise,
    MatchesRegex,
    MatchesStructure,
)
from testtools.twistedsupport import succeeded
from twisted.internet.defer import succeed
from twisted.internet.task import Clock, Cooperator
from twisted.python import runtime
from twisted.python.filepath import FilePath

from hyperlink import (
    DecodedURL,
)

from ..config import create_testing_configuration
from ..magic_file import MagicFileFactory
from ..scanner import ScannerService, find_updated_files
from ..snapshot import RemoteSnapshot, create_local_author
from ..status import FolderStatus, WebSocketStatusService
from ..util.file import PathState, get_pathinfo
from ..tahoe_client import (
    create_tahoe_client,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from .common import SyncTestCase, skipIf
from .strategies import relative_paths

# This is a path state that doesn't correspond to a file written during the test.
OLD_PATH_STATE = PathState(0, 0, 0)


class FindUpdatesTests(SyncTestCase):
    """
    Tests for ``find_updated_files``
    """

    def setup_example(self):
        self.author = create_local_author("alice")
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self._global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath("dummy"),
        )
        self.collective_cap = "URI:DIR2:mfqwcylbmfqwcylbmfqwcylbme:mfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqq"
        self.personal_cap = "URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra"

        self.clock = Clock()
        self.status_service = WebSocketStatusService(self.clock, self._global_config)
        self.folder_status = FolderStatus("default", self.status_service)

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
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text("http://invalid./"),
            create_tahoe_treq_client(create_fake_tahoe_root()),
        )

    @given(
        relative_paths(),
    )
    def test_scan_new(self, relpath):
        """
        A completely new file is scanned
        """
        local = self.magic_path.preauthChild(relpath)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")

        files = []
        self.assertThat(
            find_updated_files(
                self.cooperator, self.config, files.append, status=self.folder_status
            ),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([local]),
        )

    @skipIf(
        runtime.platformType == "win32", "windows does not have unprivileged symlinks"
    )
    @given(
        relative_paths(),
    )
    def test_scan_symlink(self, relpath):
        """
        A completely new symlink is ignored
        """
        local = self.magic_path.preauthChild(relpath)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").linkTo(local.asBytesMode("utf-8"))

        files = []
        self.assertThat(
            find_updated_files(
                self.cooperator, self.config, files.append, status=self.folder_status
            ),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([]),
        )

    @given(
        relative_paths(),
    )
    def test_scan_nothing(self, relpath):
        """
        An existing, non-updated file is not scanned
        """
        local = self.magic_path.preauthChild(relpath)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")
        snap = RemoteSnapshot(
            relpath,
            self.author,
            metadata={
                "modification_time": int(
                    local.asBytesMode("utf-8").getModificationTime()
                ),
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[],
            content_cap="URI:CHK:",
            metadata_cap="URI:CHK:",
        )
        self.config.store_downloaded_snapshot(
            relpath, snap, get_pathinfo(local).state
        )

        files = []
        self.assertThat(
            find_updated_files(
                self.cooperator, self.config, files.append, status=self.folder_status
            ),
            succeeded(Always()),
        )
        self.assertThat(files, Equals([]))

    @given(
        relative_paths(),
    )
    def test_scan_something_remote(self, relpath):
        """
        We scan an update to a file we already know about.
        """
        local = self.magic_path.preauthChild(relpath)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")
        snap = RemoteSnapshot(
            relpath,
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
            metadata_cap="URI:CHK:",
        )
        self.config.store_downloaded_snapshot(
            relpath,
            snap,
            OLD_PATH_STATE,
        )

        files = []
        self.assertThat(
            find_updated_files(
                self.cooperator, self.config, files.append, status=self.folder_status
            ),
            succeeded(Always()),
        )
        self.assertThat(files, Equals([local]))

    @given(
        relative_paths(),
    )
    def test_scan_something_local(self, relpath):
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
            find_updated_files(
                self.cooperator, self.config, files.append, status=self.folder_status
            ),
            succeeded(Always()),
        )
        self.assertThat(files, Equals([local]))

    @given(
        relative_paths(),
    )
    def test_scan_existing_to_directory(self, relpath):
        """
        When we scan a path that we have a snapshot for, but it is now a directory,
        we ignore it, and report an error.
        """
        local = self.magic_path.preauthChild("existing-file")
        local.asBytesMode("utf-8").makedirs()
        stash_dir = FilePath(self.mktemp())
        stash_dir.makedirs()

        self.config.store_currentsnapshot_state("existing-file", OLD_PATH_STATE)

        files = []
        self.assertThat(
            find_updated_files(
                self.cooperator, self.config, files.append, status=self.folder_status
            ),
            succeeded(Always()),
        )
        self.assertThat(files, Equals([]))
        self.assertThat(
            self.status_service._folders["default"]["errors"],
            MatchesListwise(
                [
                    MatchesStructure(
                        summary=MatchesRegex("File .* was a file, and now is a directory.")
                    ),
                ]
            ),
        )

    @given(
        relative_paths(),
    )
    def test_scan_once(self, relpath):
        """
        The scanner service discovers a_file when a scan is requested.
        """
        relpath = "a_file"
        local = self.magic_path.preauthChild(relpath)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")

        files = []

        class FakeLocalSnapshot(object):
            pass

        class SnapshotService(object):
            def add_file(self, f, local_parent=None):
                files.append(f)
                return succeed(FakeLocalSnapshot())

        file_factory = MagicFileFactory(
            self.config,
            self.tahoe_client,
            self.folder_status,
            SnapshotService(),
            object(), # write_participant,
            object(), # remote_cache,
            object(), # filesystem,
        )

        service = ScannerService(
            self.config,
            file_factory,
            object(),
            cooperator=self.cooperator,
            scan_interval=None,
        )
        service.startService()
        self.addCleanup(service.stopService)

        self.assertThat(
            service.scan_once(),
            succeeded(Always()),
        )

        self.assertThat(files, Equals([local]))

    @given(
        relative_paths(),
    )
    def test_scan_periodic(self, relpath):
        """
        The scanner service iteself discovers a_file, when a scan interval is
        set.
        """
        relpath = "a_file"
        local = self.magic_path.preauthChild(relpath)
        local.parent().asBytesMode("utf-8").makedirs(ignoreExistingDirectory=True)
        local.asBytesMode("utf-8").setContent(b"dummy\n")

        files = []

        class FakeLocalSnapshot(object):
            pass

        class SnapshotService(object):
            def add_file(self, f, local_parent=None):
                files.append(f)
                return succeed(
                    FakeLocalSnapshot()
                )

        file_factory = MagicFileFactory(
            self.config,
            self.tahoe_client,
            self.folder_status,
            SnapshotService(),
            object(), #self.write_participant,
            object(), #self.remote_cache,
            object(), #self.filesystem,
        )

        service = ScannerService(
            self.config,
            file_factory,
            object(),
            cooperator=self.cooperator,
            scan_interval=1,
        )
        service.startService()
        self.addCleanup(service.stopService)

        self.assertThat(files, Equals([local]))
