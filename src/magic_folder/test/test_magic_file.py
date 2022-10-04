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
from zope.interface import (
    implementer,
)
import attr

from ..config import (
    create_testing_configuration,
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
)
from .common import (
    SyncTestCase,
)




@implementer(IParticipant)
@attr.s
class _FakeParticipant(object):
    our_files = attr.ib()

    def files(self):
        return self.our_files


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
