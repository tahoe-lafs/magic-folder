import io

import attr

from zope.interface import (
    implementer,
)

from testtools.matchers import (
    MatchesPredicate,
    Always,
    Equals,
)
from testtools import (
    ExpectedException,
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
    IRemoteSnapshotCreator,
    UploaderService,
    RemoteSnapshotCreator,
)
from ..config import (
    create_global_configuration,
    SnapshotNotFound,
)
from ..snapshot import (
    create_local_author,
    create_snapshot,
)
from twisted.internet import task

from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
)

from magic_folder.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)
from allmydata.uri import is_uri

class RemoteSnapshotCreatorTests(SyncTestCase):
    """
    Tests for ``RemoteSnapshotCreator``.
    """
    def setUp(self):
        super(RemoteSnapshotCreatorTests, self).setUp()
        self.author = create_local_author("alice")

    def setup_example(self):
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com"),
            self.http_client,
        )

        self.temp = FilePath(self.mktemp())
        self.magic_path = self.temp.child(b"magic")
        self.magic_path.makedirs()

        global_config = create_global_configuration(
            self.temp.child(b"global-db"),
            u"tcp:12345",
            self.temp.child(b"tahoe-node"),
        )

        self.poll_interval = 1

        self.state_db = global_config.create_magic_folder(
            u"some-folder",
            self.magic_path,
            self.temp.child(b"state"),
            self.author,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            self.poll_interval,
        )

        self.remote_snapshot_creator = RemoteSnapshotCreator(
            state_db=self.state_db,
            local_author=self.author,
            tahoe_client=self.tahoe_client,
        )

    @given(name=path_segments(),
           content=binary(),
    )
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
            snapshot_stash_dir=self.state_db.stash_path,
            parents=[],
        )

        snapshots = []
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        # push LocalSnapshot object into the SnapshotStore.
        # This should be picked up by the Uploader Service and should
        # result in a snapshot cap.
        self.state_db.store_local_snapshot(snapshots[0])

        d = self.remote_snapshot_creator.upload_local_snapshots()
        self.assertThat(
            d,
            succeeded(Always()),
        )

        remote_snapshot_cap = self.state_db.get_remotesnapshot(name)

        # test whether we got a capability
        self.assertThat(
            remote_snapshot_cap,
            MatchesPredicate(is_uri,
                             "%s is not a Tahoe-LAFS URI"),
        )

        with ExpectedException(SnapshotNotFound, ""):
            self.state_db.get_local_snapshot(name, self.author)


@implementer(IRemoteSnapshotCreator)
@attr.s
class MemorySnapshotCreator(object):
    _uploaded = attr.ib(default=0)

    def upload_local_snapshots(self):
        self._uploaded += 1


class UploaderServiceTests(SyncTestCase):
    """
    Tests for ``UploaderService``.
    """
    def setUp(self):
        super(UploaderServiceTests, self).setUp()
        self.poll_interval = 1
        self.clock = task.Clock()
        self.remote_snapshot_creator = MemorySnapshotCreator()
        self.uploader_service = UploaderService(
            poll_interval=self.poll_interval,
            clock=self.clock,
            remote_snapshot_creator=self.remote_snapshot_creator,
        )

    def test_commit_a_file(self):
        # start Uploader Service
        self.uploader_service.startService()
        self.addCleanup(self.uploader_service.stopService)

        # We want processing to start immediately on startup in case there was
        # work left over from the last time we ran.  So there should already
        # have been one upload attempt by now.
        self.assertThat(
            self.remote_snapshot_creator._uploaded,
            Equals(1),
        )

        # advance the clock manually, which should result in the
        # polling of the db for uncommitted LocalSnapshots in the db
        # and then check for remote snapshots
        self.clock.advance(self.poll_interval)

        self.assertThat(
            self.remote_snapshot_creator._uploaded,
            Equals(2),
        )
