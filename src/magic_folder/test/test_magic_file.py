"""
Test helper functions in magic_file
"""

from testtools.matchers import (
    Equals,
)
from twisted.internet.task import (
    Clock,
)
from twisted.internet.defer import (
    fail,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    inlineCallbacks,
    DeferredList,
)
from twisted.application.service import (
    Service,
    MultiService,
)

from nacl.signing import (
    VerifyKey,
)

from ..config import (
    create_testing_configuration,
    Conflict,
)
from ..testing.web import (
    create_tahoe_treq_client,
)
from ..downloader import (
    InMemoryMagicFolderFilesystem,
    RemoteSnapshotCacheService,
)
from ..participants import (
    SnapshotEntry,
    static_participants,
)
from ..snapshot import (
    create_local_author,
    create_author,
    RemoteSnapshot,
)
from ..status import (
    FolderStatus,
    EventsWebSocketStatusService,
)
from ..util.capabilities import (
    Capability,
    random_immutable,
    random_dircap,
)
from ..util.file import (
    PathState,
)
from ..magic_file import (
    maybe_update_personal_dmd_to_local,
    MagicFileFactory,
    ResolutionError,
)
from ..magic_folder import (
    MagicFolder,
)
from ..uploader import (
    LocalSnapshotService,
    LocalSnapshotCreator,
    InMemoryUploaderService,
)
from .common import (
    SyncTestCase,
    AsyncTestCase,
)


class StateSyncTests(SyncTestCase):
    """
    Correct operations of maybe_update_personal_dmd_to_local and helpers
    """

    def setUp(self):
        super(StateSyncTests, self).setUp()
        self.author = create_local_author("alice")
        self.participants = static_participants()

        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self._global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath("dummy"),
        )
        self.collective_cap = Capability.from_string("URI:DIR2:mfqwcylbmfqwcylbmfqwcylbme:mfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqq")
        self.personal_cap = Capability.from_string("URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra")

        self.config = self._global_config.create_magic_folder(
            "default",
            self.magic_path,
            self.author,
            self.collective_cap,
            self.personal_cap,
            1,
            None,
        )

    def test_update_snapshot(self):
        """
        A mismatch causes an update to be done
        """
        clock = Clock()
        remote_cap = random_dircap(readonly=True)
        local_cap = random_dircap(readonly=True)
        self.participants.participants[0].my_files = {
            "foo": SnapshotEntry(remote_cap, {}),
        }

        ps = PathState(size=1234, mtime_ns=42, ctime_ns=99)
        snap = RemoteSnapshot(
            "foo",
            self.author,
            {},
            capability=local_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.config.store_downloaded_snapshot("foo", snap, ps)

        # so, we've got a mismatch here: our Personal DMD has
        # "remote_cap" while our configuration says we have
        # "local_cap" on disk

        maybe_update_personal_dmd_to_local(
            clock, self.config, lambda: (self.participants.participants[0], self.participants.writer)
        )

        self.assertThat(
            self.participants.writer.updates,
            Equals([
                ("foo", local_cap),
            ])
        )

    def test_retry_on_error(self):
        """
        An error causes a retry
        """
        clock = Clock()
        remote_cap = random_dircap(readonly=True)
        local_cap = random_dircap(readonly=True)
        self.participants.participants[0].my_files = {
            "foo": SnapshotEntry(remote_cap, {}),
        }

        ps = PathState(size=1234, mtime_ns=42, ctime_ns=99)
        snap = RemoteSnapshot(
            "foo",
            self.author,
            {},
            capability=local_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.config.store_downloaded_snapshot("foo", snap, ps)
        self.config.store_downloaded_snapshot("bar", snap, ps)

        # so, we've got a mismatch here: our Personal DMD has
        # "remote_cap" while our configuration says we have
        # "local_cap" on disk

        # also, arrange to have the first attempt to fix this cause an
        # error (from e.g. Tahoe)

        errors = [True]

        def error_then_succeed(relpath, cap):
            if errors:
                errors.pop()
                return fail(Exception("something went wrong"))
            return original(relpath, cap)
        original = self.participants.writer.update_snapshot
        self.participants.writer.update_snapshot = error_then_succeed

        # let it update
        maybe_update_personal_dmd_to_local(
            clock, self.config, lambda: (self.participants.participants[0], self.participants.writer)
        )

        # ... but we need to wait 5 seconds to get another try, due to the error
        clock.advance(5)

        self.assertThat(
            self.participants.writer.updates,
            Equals([
                ("foo", local_cap),
                ("bar", local_cap),
            ])
        )


class RemoteUpdateTests(AsyncTestCase):
    """
    Correct operation of remote updates
    """

    def setUp(self):
        super(RemoteUpdateTests, self).setUp()
        # avoid global import
        from twisted.internet import reactor
        self.reactor = reactor
        self.author = create_local_author("alice")
        self.participants = static_participants()

        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self._global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath("dummy"),
        )
        self.collective_cap = Capability.from_string("URI:DIR2:mfqwcylbmfqwcylbmfqwcylbme:mfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqq")
        self.personal_cap = Capability.from_string("URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra")

        self.config = self._global_config.create_magic_folder(
            "default",
            self.magic_path,
            self.author,
            self.collective_cap,
            self.personal_cap,
            1,
            None,
        )

        tahoe_client = object()
        self.uploader = InMemoryUploaderService(["a-file-name", "a-file-name"])
        status_service = EventsWebSocketStatusService(self.reactor, self._global_config)
        folder_status = FolderStatus("folder-name", status_service)
        self.stash_path = FilePath(self.mktemp())
        self.stash_path.makedirs()
        self.local_snapshot_service = LocalSnapshotService(
            self.config,
            LocalSnapshotCreator(
                self.config,
                self.author,
                self.stash_path,
                self.magic_path,
                object(),
            ),
            folder_status,
        )
        self.filesystem = InMemoryMagicFolderFilesystem()

        self.tahoe_client = create_tahoe_treq_client()
        self.remote_cache = RemoteSnapshotCacheService.from_config(self.config, self.tahoe_client)

        self.magic_file_factory = MagicFileFactory(
            self.config,
            tahoe_client,
            folder_status,
            self.local_snapshot_service,
            self.uploader,
            self.participants.writer,
            self.remote_cache,
            self.filesystem,
        )
        self.magic_folder = MagicFolder(
            client=tahoe_client,
            config=self.config,
            name="folder-name",
            invite_manager=Service(),
            local_snapshot_service=self.local_snapshot_service,
            folder_status=folder_status,
            remote_snapshot_cache=self.remote_cache,
            downloader=MultiService(),
            uploader=self.uploader,
            participants=self.participants,
            scanner_service=Service(),
            clock=self.reactor,
            magic_file_factory=self.magic_file_factory,
        )
        self.magic_folder.startService()

    def tearDown(self):
        super(RemoteUpdateTests, self).tearDown()
        d0 = self.magic_folder.stopService()
        d1 = self.local_snapshot_service.stopService()
        return DeferredList([d0, d1])

    @inlineCallbacks
    def test_multiple_local_updates(self):
        """
        If we trigger multiple updates to a local file quickly (could be
        done via API for example) then local snapshot are produced in
        order.
        """
        self.magic_path.child("a-file-name").setContent(b"file data zero\n" * 1000)
        d0 = self.magic_folder.add_snapshot("a-file-name")
        d1 = self.magic_folder.add_snapshot("a-file-name")

        results = yield DeferredList([d0, d1])
        for ok, snap in results:
            assert ok, "a snapshot failed"

    @inlineCallbacks
    def test_multiple_remote_updates(self):
        """
        If we are scanning a multi-participant folder and 2 or more have
        updates, we can easily trigger multiple identical
        updates. This should not result in a conflict.
        """
        relpath = "multiple-remote"
        cap0 = random_immutable(directory=True)
        remote0 = RemoteSnapshot(
            relpath=relpath,
            author=create_author("someone", VerifyKey(b"\xff" * 32)),
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[cap0.danger_real_capability_string()] = remote0
        abspath = self.config.magic_path.preauthChild(relpath)
        mf = self.magic_file_factory.magic_file_for(abspath)
        self.participants.add("beth", random_dircap())
        self.participants.add("callum", random_dircap())
        self.participants.add("dawn", random_dircap())
        d0 = mf.found_new_remote(remote0, self.participants.participants[1])
        d1 = mf.found_new_remote(remote0, self.participants.participants[2])
        d2 = mf.found_new_remote(remote0, self.participants.participants[3])
        yield DeferredList([d0, d1, d2])

    @inlineCallbacks
    def test_not_conflicted(self):
        """
        """
        relpath = "not-conflict"
        abspath = self.config.magic_path.preauthChild(relpath)
        mf = self.magic_file_factory.magic_file_for(abspath)
        with self.assertRaises(ResolutionError):
            yield mf.resolve_conflict("foo")

    @inlineCallbacks
    def test_remote_conflict(self):
        """
        We have a conflict
        """
        relpath = "dual-conflict"
        cap0 = random_immutable(directory=True)
        cap1 = random_immutable(directory=True)
        remote0 = RemoteSnapshot(
            relpath=relpath,
            author=create_author("someone", VerifyKey(b"\xff" * 32)),
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        remote1 = RemoteSnapshot(
            relpath=relpath,
            author=create_author("someone_else", VerifyKey(b"\xff" * 32)),
            metadata={"modification_time": 0},
            capability=cap1,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[cap0.danger_real_capability_string()] = remote0
        self.remote_cache._cached_snapshots[cap1.danger_real_capability_string()] = remote1

        abspath = self.config.magic_path.preauthChild(relpath)
        mf = self.magic_file_factory.magic_file_for(abspath)
        self.participants.add("beth", random_dircap())
        d0 = mf.found_new_remote(remote0, self.participants.participants[0])
        d1 = mf.found_new_remote(remote1, self.participants.participants[1])
        yield DeferredList([d0, d1])

        self.assertEquals(
            self.config.list_conflicts_for("dual-conflict"),
            [Conflict(snapshot_cap=cap1, participant_name="beth")]
        )

        self.uploader._uploads = ["dual-conflict"]
        snap = yield mf.resolve_conflict(Conflict(snapshot_cap=cap1, participant_name="beth"))
        print("XXX", snap)

    @inlineCallbacks
    def test_remote_conflict_resolution(self):
        """
        an incoming update is actually a resolution to an existing conflict
        """
        relpath = "fire"
        with self.magic_path.child(relpath).open("wb") as f:
            f.write(b"testdata")

        from magic_folder.util.file import get_pathinfo
        cap0 = random_immutable(directory=True)
        cap1 = random_immutable(directory=True)
        cap2 = random_immutable(directory=True)
        remote0 = RemoteSnapshot(
            relpath=relpath,
            author=create_author("me", VerifyKey(b"\xff" * 32)),
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        remote1 = RemoteSnapshot(
            relpath=relpath,
            author=create_author("zara", VerifyKey(b"\xff" * 32)),
            metadata={"modification_time": 0},
            capability=cap1,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        remote2 = RemoteSnapshot(
            relpath=relpath,
            author=create_author("yidris", VerifyKey(b"\xff" * 32)),
            metadata={"modification_time": 0},
            capability=cap2,
            parents_raw=[remote0.capability.danger_real_capability_string(), remote1.capability.danger_real_capability_string()],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        assert cap0 == remote0.capability
        assert cap1 == remote1.capability
        assert cap2 == remote2.capability
        self.remote_cache._cached_snapshots[cap0.danger_real_capability_string()] = remote0
        self.remote_cache._cached_snapshots[cap1.danger_real_capability_string()] = remote1
        self.remote_cache._cached_snapshots[cap2.danger_real_capability_string()] = remote2

        self.participants.add("zara", random_dircap())
        self.participants.add("yidris", random_dircap())

        self.config.store_currentsnapshot_state(relpath, get_pathinfo(self.magic_path.child(relpath)).state)
        self.config.store_uploaded_snapshot(relpath, remote0, 0.0)
        self.filesystem._conflicted_paths.add(relpath)
        self.config.add_conflict(remote1, self.participants.participants[1])

        abspath = self.config.magic_path.preauthChild(relpath)
        mf = self.magic_file_factory.magic_file_for(abspath)

        self.assertEquals(
            self.config.list_conflicts_for(relpath),
            [Conflict(snapshot_cap=cap1, participant_name="zara")]
        )

        # resolve the conflict via a remote
        yield mf.found_new_remote(remote2, self.participants.participants[2])

        self.assertEquals(
            self.config.list_conflicts_for(relpath),
            []
        )
