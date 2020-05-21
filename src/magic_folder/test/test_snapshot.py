import os

from twisted.internet import defer
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.resource import (
    Resource,
)
from twisted.web.client import (
    Agent,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    RequestTraversalAgent,
)

from magic_folder.snapshot import create_snapshot

from .fixtures import (
    NodeDirectory,
)
from .common import (
    ShouldFailMixin,
    SyncTestCase,
    AsyncTestCase,
    skipIf,
)


class FakeTahoeRoot(Resource):
    """
    This is a sketch of how an in-memory 'fake' of a Tahoe
    WebUI. Ultimately, this will live in Tahoe
    """


class TahoeSnapshotTest(AsyncTestCase):
    """
    Tests for the snapshots
    """

    @defer.inlineCallbacks
    def setUp(self):
        """
        Create a Tahoe-LAFS node which contain some magic-folder configuration
        and run it.
        """
        yield super(TahoeSnapshotTest, self).setUp()
        self._nodedir = self.useFixture(
            NodeDirectory(
                path=FilePath(self.mktemp()),
            )
        )
        self._tahoe_root = FakeTahoeRoot()
        self._agent = RequestTraversalAgent(self._tahoe_root)

    @defer.inlineCallbacks
    def test_create_new_tahoe_snapshot(self):
        """
        create a new snapshot (this will have no parent snapshots).
        """

        # Get a magic folder.
        folder_path = self._nodedir.path.child(u"magic-folder")

        os.mkdir(folder_path.asBytesMode().path)
        file_path = folder_path.child("foo")
        file_path.touch()

        treq = HTTPClient(self._agent)

        # XXX THINK: do we really need a full node-dir ?
        snapshot = yield create_snapshot(
            node_directory=self._nodedir.path,
            filepath=file_path,
            parents=[],
            treq=treq,
        )

        self.assertThat(snapshot.capability, StartsWith("URI:DIR2-CHK:"))

    @defer.inlineCallbacks
    def _test_snapshot_extend(self):
        """
        create a new snapshot and extend it with a new snapshot.
        """

        os.mkdir(folder_path.asBytesMode().path)
        file_path = folder_path.child("foo")
        file_path.touch()

        from twisted.internet import reactor
        treq = HTTPClient(Agent(reactor))

        snapshot = TahoeSnapshot(node_directory=self.node_directory, filepath=file_path)

        initial_snapshot_uri = yield snapshot.create_snapshot([], treq)

        self.assertThat(initial_snapshot_uri, StartsWith("URI:DIR2-CHK:"))

        # write something to the file
        with open(file_path.asBytesMode().path, "w") as f:
            f.write("foobar")

        snapshot_uri = yield snapshot.create_snapshot([initial_snapshot_uri], treq)

        self.assertThat(snapshot_uri, StartsWith("URI:DIR2-CHK:"))

        # the returned URI should be different from the first snapshot URI
        self.assertNotEqual(snapshot_uri, initial_snapshot_uri)

