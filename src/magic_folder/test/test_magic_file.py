"""
Test helper functions in magic_file
"""

from testtools.matchers import (
    Equals,
)
from twisted.internet.task import (
    Clock,
    deferLater,
)
from twisted.internet.defer import (
    fail,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    inlineCallbacks,
    Deferred,
    DeferredList,
    succeed,
)
from zope.interface import (
    implementer,
)
import attr

from ..config import (
    create_testing_configuration,
)
from ..downloader import (
    InMemoryMagicFolderFilesystem,
)
from ..participants import (
    IParticipant,
    IWriteableParticipant,
    SnapshotEntry,
)
from ..snapshot import (
    create_local_author,
    RemoteSnapshot,
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
)
from .common import (
    SyncTestCase,
    AsyncTestCase,
)




@implementer(IParticipant)
@attr.s
class _FakeParticipant(object):
    our_files = attr.ib()
    _is_self = attr.ib(default=False)

    def files(self):
        return self.our_files

    def is_self(self):
        return self._is_self


@implementer(IWriteableParticipant)
@attr.s
class _FakeWriteableParticipant(object):
    updates = attr.ib(factory=list)

    def update_snapshot(self, relpath, capability):
        self.updates.append((relpath, capability))


class StateSyncTests(SyncTestCase):
    """
    Correct operations of maybe_update_personal_dmd_to_local and helpers
    """

    def setUp(self):
        super(StateSyncTests, self).setUp()
        self.author = create_local_author("alice")
        self.read_participant = _FakeParticipant({})
        self.write_participant = _FakeWriteableParticipant()

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
        self.read_participant.our_files = {
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
            clock, self.config, self.read_participant, self.write_participant,
        )

        self.assertThat(
            self.write_participant.updates,
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
        self.read_participant.our_files = {
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
        original = self.write_participant.update_snapshot
        self.write_participant.update_snapshot = error_then_succeed

        # let it update

        maybe_update_personal_dmd_to_local(
            clock, self.config, self.read_participant, self.write_participant,
        )

        # ... but we need to wait 5 seconds to get another try, due to the error
        clock.advance(5)

        self.assertThat(
            self.write_participant.updates,
            Equals([
                ("foo", local_cap),
                ("bar", local_cap),
            ])
        )


class RemoteUpdateTests(AsyncTestCase):
    """
    Correct operations of maybe_update_personal_dmd_to_local and helpers
    """

    def setUp(self):
        super(RemoteUpdateTests, self).setUp()
        self.author = create_local_author("alice")
        self.read_participant = _FakeParticipant({})
        self.write_participant = _FakeWriteableParticipant()

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

    @inlineCallbacks
    def test_multiple_updates(self):
        """
        If multiple other participants have updated to the same Snapshot
        before a poll, the state-machine will re-enter _downloading
        with same Snapshot. It should handle this and not produce a
        Conflict.
        """

        from twisted.internet import reactor
        clock = reactor#Clock()
        remote_cap = random_dircap(readonly=True)
        local_cap = random_dircap(readonly=True)
        self.read_participant.our_files = {
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
        tahoe_client = object()

        from .test_magic_folder_service import _FakeParticipants
        from twisted.application.service import (
            Service,
            MultiService,
        )
        from ..status import (
            FolderStatus,
            WebSocketStatusService,
        )
        from ..magic_folder import MagicFolder
        from ..uploader import LocalSnapshotService, LocalSnapshotCreator

        participants = _FakeParticipants(
            self.write_participant,
            [
                self.read_participant,
            ],
        )

        from ..uploader import InMemoryUploaderService
        uploader = InMemoryUploaderService([True, True])
        status_service = WebSocketStatusService(clock, self._global_config)
        folder_status = FolderStatus("folder-name", status_service)
        stash_path = FilePath(self.mktemp())
        stash_path.makedirs()
        local_snapshot_service = LocalSnapshotService(
            self.config,
            LocalSnapshotCreator(
                self.config,
                self.author,
                stash_path,
                self.magic_path,
                object(),
            ),
            folder_status,
        )
        filesystem = InMemoryMagicFolderFilesystem()

        class FakeRemoteCache(Service):
            _cached_snapshots = dict()
        remote_cache = FakeRemoteCache()

        magic_folder = MagicFolder(
            client=tahoe_client,
            config=self.config,
            name="folder-name",
            invite_manager=Service(),
            local_snapshot_service=local_snapshot_service,
            folder_status=folder_status,
            remote_snapshot_cache=remote_cache,
            downloader=MultiService(),
            uploader=uploader,
            participants=participants,
            scanner_service=Service(),
            clock=clock,
            magic_file_factory=MagicFileFactory(
                self.config,
                tahoe_client,
                folder_status,
                local_snapshot_service,
                uploader,
                self.write_participant,
                remote_cache,
                filesystem,
            ),
        )

        magic_folder.startService()
        self.magic_path.child("a-file-name").setContent(b"file data zero\n" * 1000)
        d0 = magic_folder.add_snapshot("a-file-name")
        self.magic_path.child("a-file-name").setContent(b"file data one\n" * 1000)
        d1 = magic_folder.add_snapshot("a-file-name")

        results = yield DeferredList([d0, d1])
        for ok, snap in results:
            assert ok, "a snapshot failed"

        print("HIHI")

        yield magic_folder.stopService()
        yield local_snapshot_service.stopService()
