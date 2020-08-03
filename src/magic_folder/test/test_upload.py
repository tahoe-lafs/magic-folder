import io
import attr
from tempfile import mktemp

from testtools.matchers import (
    StartsWith,
)
from testtools.twistedsupport import (
    succeeded,
)
from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
)
from twisted.python.filepath import (
    FilePath,
)
from hyperlink import (
    DecodedURL,
)
from ..magic_folder import (
    UploaderService,
)
from magic_folder.snapshot import (
    create_local_author,
    create_snapshot,
)
from twisted.internet import reactor
from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
)
from eliot import (
    Message,
)
from magic_folder.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)

@attr.s
class MemorySnapshotStore(object):
    """
    A way to test Uploader Service with an in-memory database.

    :ivar [FilePath] processed: All of the paths passed to ``process_item``,
        in the order they were passed.
    """
    local_processed = attr.ib(default=attr.Factory(list))
    remote_processed = attr.ib(default=attr.Factory(list))

    def store_local_snapshot(self, snapshot):
        Message.log(
            message_type=u"memory-snapshot-store:store-local-snapshot",
            path=snapshot.name,
        )
        name = snapshot.name
        self.local_processed.append((name, snapshot))

    def get_all_item_paths(self):
        return map(
            lambda name_snapshot_pair: name_snapshot_pair[0],
            self.local_processed,
        )

    def get_local_snapshot(self, path, _unused):
        for (name, snapshot) in self.local_processed:
            if name is path:
                return snapshot
        return None

    def remove_localsnapshot(self, path):
        for p in self.local_processed:
            (name, snapshot) = p
            if name is path:
                self.local_processed.remove(p)

    def store_remote_snapshot(self, path, cap):
        self.remote_processed.append((path, cap))

    def get_remote_snapshot_cap(self, path):
        for (p, remote_snapshot) in self.remote_processed:
            if p is path:
                return remote_snapshot.content_cap

class UploaderServiceTests(SyncTestCase):
    """
    Tests for ``UploaderService``.
    """
    def setUp(self):
        super(UploaderServiceTests, self).setUp()
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self.author = create_local_author("alice")
        self.stash_dir = FilePath(mktemp())
        self.stash_dir.makedirs()

    def setup_example(self):
        """
        per-example state that is invoked by hypothesis.
        """
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com"),
            self.http_client,
        )
        self.snapshot_store = MemorySnapshotStore()
        self.uploader_service = UploaderService(
            snapshot_store=self.snapshot_store,
            local_author = self.author,
            tahoe_client=self.tahoe_client,
            clock=reactor,
        )

    @given(path_segments(), binary())
    def test_commit_a_file(self, name, content):
        """
        Add a file into localsnapshot store, start the service which
        should result in a remotesnapshot corresponding to the
        localsnapshot.
        """

        # create a local snapshot
        data = io.BytesIO(content)

        d = create_snapshot(
            name=name,
            author=self.author,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
        )

        # push LocalSnapshot object into the SnapshotStore.
        d.addCallback(self.snapshot_store.store_local_snapshot)

        # start Uploader Service
        self.uploader_service.startService()

        # this should be picked up by the Uploader Service and should
        # result in a snapshot cap.
        d.addCallback(lambda _unused:
                      self.snapshot_store.get_remote_snapshot_cap(name))

        # test whether we got a capability
        self.assertThat(
            d,
            succeeded(
                StartsWith("URI:CHK:"),
            ),
        )

        self.uploader_service.stopService()
