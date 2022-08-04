
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
from twisted.internet.defer import (
    succeed,
    Deferred,
)
from twisted.internet.task import Clock, Cooperator
from twisted.python import runtime
from twisted.python.filepath import FilePath

from hyperlink import (
    DecodedURL,
)

from ..config import create_testing_configuration
from ..magic_file import MagicFileFactory
from ..scanner import (
    ScannerService,
    find_updated_files,
    find_deleted_files,
)
from ..snapshot import (
    LocalSnapshot,
    RemoteSnapshot,
    create_local_author,
)
from ..status import FolderStatus, WebSocketStatusService
from ..util.file import (
    PathState,
    get_pathinfo,
    seconds_to_ns,
)
from ..util.capabilities import (
    random_dircap,
    random_immutable,
)
from ..tahoe_client import (
    create_tahoe_client,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from .common import SyncTestCase, skipIf
from .strategies import (
    relative_paths,
    path_segments,
 )

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
        self.collective_cap = random_dircap()
        self.personal_cap = random_dircap()

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
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.setContent(b"dummy\n")

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
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.linkTo(local)

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
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.setContent(b"dummy\n")
        snap = RemoteSnapshot(
            relpath,
            self.author,
            metadata={
                "modification_time": int(
                    local.getModificationTime()
                ),
            },
            capability=random_immutable(directory=True),
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
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
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.setContent(b"dummy\n")
        snap = RemoteSnapshot(
            relpath,
            self.author,
            metadata={
                # this remote is 2min older than our local file
                "modification_time": int(
                    local.getModificationTime()
                )
                - 120,
            },
            capability=random_immutable(directory=True),
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
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
        local.setContent(b"dummy\n")
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
        local.makedirs()
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
    def test_scan_delete(self, relpath):
        """
        Scanning a relpath we know about with no local file is a delete
        """
        self.config.store_currentsnapshot_state(relpath, OLD_PATH_STATE)

        files = []
        self.assertThat(
            find_deleted_files(
                self.cooperator, self.config, files.append, status=self.folder_status
            ),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([self.config.magic_path.preauthChild(relpath)])
        )

    @given(
        relative_paths(),
    )
    def test_scan_once(self, relpath):
        """
        The scanner service discovers a_file when a scan is requested.
        """
        relpath = "a_file0"
        local = self.magic_path.preauthChild(relpath)
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.setContent(b"dummy\n")

        files = []

        class FakeLocalSnapshot(object):
            pass

        class SnapshotService(object):
            def add_file(self, f, local_parent=None):
                files.append(f)
                return succeed(FakeLocalSnapshot())

        class FakeUploader(object):
            def upload_snapshot(self, snapshot):
                return Deferred()

        file_factory = MagicFileFactory(
            self.config,
            self.tahoe_client,
            self.folder_status,
            SnapshotService(),
            FakeUploader(),
            object(), # write_participant,
            object(), # remote_cache,
            object(), # filesystem,
            synchronous=True,
        )

        service = ScannerService(
            self.config,
            file_factory,
            object(),
            cooperator=self.cooperator,
            scan_interval=None,
            clock=self.clock,
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
        relpath = "a_file1"
        local = self.magic_path.preauthChild(relpath)
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.setContent(b"dummy\n")

        files = []

        class FakeLocalSnapshot(object):
            pass

        class SnapshotService(object):
            def add_file(self, f, local_parent=None):
                files.append(f)
                return succeed(
                    FakeLocalSnapshot()
                )

        class FakeRemoteSnapshot(object):
            pass

        class FakeUploader(object):
            def upload_snapshot(self, snapshot):
                return Deferred()

        file_factory = MagicFileFactory(
            self.config,
            self.tahoe_client,
            self.folder_status,
            SnapshotService(),
            FakeUploader(),
            object(), # write_participant,
            object(), # remote_cache,
            object(), # filesystem,
            synchronous=True,
        )

        service = ScannerService(
            self.config,
            file_factory,
            object(),
            cooperator=self.cooperator,
            scan_interval=1,
            clock=self.clock,
        )
        service.startService()
        self.addCleanup(service.stopService)

        self.assertThat(files, Equals([local]))

    # XXX should use generated author-names, but get encoding errors
    # from FilePath on u'0.conflict-\x00' -- that is, a single nul as
    # an author-name
    @given(
        relative_paths(),
    )
    def test_scan_conflict_files(self, relpath):
        """
        A completely new file is found but it is a conflict-marker file
        and shouldn't be uploaded
        """
        local = self.magic_path.preauthChild(relpath + u".conflict-author")
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.setContent(b"dummy\n")

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
            object(), # uploader,
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
            clock=self.clock,
        )
        service.startService()
        self.addCleanup(service.stopService)

        self.assertThat(
            service.scan_once(),
            succeeded(Always()),
        )

        self.assertThat(files, Equals([]))

    @given(
        path_segments(),
    )
    def test_scan_preexisting_local_snapshot(self, relpath):
        """
        A pre-existing LocalSnapshot is in our database so the scanner
        finds it at startup
        """
        # make a pre-existing local snapshot
        self.setup_example()
        local = self.magic_path.preauthChild(relpath)
        local.parent().makedirs(ignoreExistingDirectory=True)
        local.setContent(b"dummy\n")
        # pretend we stashed it, too
        stash = FilePath(self.mktemp())
        stash.makedirs()
        stash.child(relpath).setContent(b"dummy\n")

        local_snap = LocalSnapshot(
            relpath,
            author=self.author,
            metadata={
                "modification_time": 1234,
            },
            content_path=stash.child(relpath),
            parents_local=[],
        )
        self.config.store_local_snapshot(
            local_snap,
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )

        files = []

        class SnapshotService(object):
            def add_file(self, f):
                files.append(f)
                return succeed(local_snap)

        class FakeUploader(object):
            def upload_snapshot(self, snapshot):
                return Deferred()

        file_factory = MagicFileFactory(
            self.config,
            self.tahoe_client,
            self.folder_status,
            SnapshotService(),
            FakeUploader(),
            object(), # write_participant,
            object(), # remote_cache,
            object(), # filesystem,
            synchronous=True,
        )

        service = ScannerService(
            self.config,
            file_factory,
            object(),
            cooperator=self.cooperator,
            scan_interval=None,
            clock=self.clock,
        )
        service.startService()
        self.addCleanup(service.stopService)

        self.assertThat(
            service._loop(),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([local])
        )
