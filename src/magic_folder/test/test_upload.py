import io

from testtools.matchers import (
    MatchesPredicate,
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
    RemoteSnapshotCreator,
)
from ..config import (
    create_global_configuration,
)
from ..snapshot import (
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

from magic_folder.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)
from allmydata.uri import is_uri

class UploaderServiceTests(SyncTestCase):
    """
    Tests for ``UploaderService``.
    """
    def setUp(self):
        super(UploaderServiceTests, self).setUp()
        self.author = create_local_author("alice")

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

        self.temp = FilePath(self.mktemp())
        self.global_db = create_global_configuration(
            self.temp.child(b"global-db"),
            u"tcp:12345",
            self.temp.child(b"tahoe-node"),
        )
        self.magic_path = self.temp.child(b"magic")
        self.magic_path.makedirs()
        self.state_db = self.global_db.create_magic_folder(
            u"some-folder",
            self.magic_path,
            self.temp.child(b"state"),
            self.author,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            60,
        )

        self.remote_snapshot_creator = RemoteSnapshotCreator(
            state_db=self.state_db,
            local_author = self.author,
        )

        self.uploader_service = UploaderService(
            tahoe_client=self.tahoe_client,
            clock=reactor,
            polling_interval=1,
            remote_snapshot_creator=self.remote_snapshot_creator,
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

        # push LocalSnapshot object into the SnapshotStore.
        d.addCallback(self.state_db.store_local_snapshot)

        # start Uploader Service
        self.uploader_service.startService()

        # this should be picked up by the Uploader Service and should
        # result in a snapshot cap.

        # XXX: There is a race condition here. We are checking for a
        # remotesnapshot in the db immediately. This works only
        # because we started the service just above this function. Had
        # it been running for a while, then we would have to wait for
        # at the most the polling interval + some processing time for
        # the db to appear on the database.

        d.addCallback(lambda _unused:
                      self.state_db.get_remotesnapshot(name))

        # test whether we got a capability
        self.assertThat(
            d,
            succeeded(
                MatchesPredicate(is_uri,
                                 "%s is not a Tahoe-LAFS URI"),
            ),
        )

        self.addCleanup(self.uploader_service.stopService)
