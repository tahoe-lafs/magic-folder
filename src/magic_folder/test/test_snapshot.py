import io
import os

from nacl.signing import (
    SigningKey,
    VerifyKey,
)

from twisted.internet import defer
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.resource import (
    Resource,
)
from twisted.web.client import (
    Agent,
    FileBodyProducer,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    RequestTraversalAgent,
    RequestSequence,
    StubTreq,
    _SynchronousProducer,  # FIXME copy code somewhere, "because private"
)

from hyperlink import (
    DecodedURL,
)

from testtools.matchers import (
    StartsWith,
)

from .fixtures import (
    NodeDirectory,
)
from .common import (
    ShouldFailMixin,
    SyncTestCase,
    AsyncTestCase,
    skipIf,
)
from magic_folder.snapshot import (
    create_author,
    create_author_from_json,
    create_snapshot,
    create_snapshot_from_capability,
    write_snapshot_to_tahoe,
)
from magic_folder.tahoe_client import (
    TahoeClient,
)


class _FakeTahoeRoot(Resource):
    """
    This is a sketch of how an in-memory 'fake' of a Tahoe
    WebUI. Ultimately, this will live in Tahoe
    """


class _FakeTahoeUriHandler(Resource):
    """
    """

    isLeaf = True

    def render(self, request):
        return b"URI:DIR2-CHK:some capability"


def create_fake_tahoe_root():
    """
    Probably should take some params to control what this fake does:
    return errors, pre-populate capabilities, ...
    """
    root = _FakeTahoeRoot()
    root.putChild(
        b"uri",
        _FakeTahoeUriHandler(),
    )
    return root




class TestSnapshotAuthor(AsyncTestCase):
    """
    """
    def setUp(self):
        """
        We have Alices's signing+verify key but only a verify key for Bob
        (in the SnapshotAuthor instance)
        """
        d = super(TestSnapshotAuthor, self).setUp()
        self.alice = create_author("alice")
        return d

    def test_author_serialize(self):
        js = self.alice.to_json_private()
        for k in ['name', 'signing_key', 'verify_key']:
            self.assertIn(k, js)
        alice2 = create_author_from_json(js)
        self.assertEqual(self.alice, alice2)

    def test_author_serialize_public(self):
        js = self.alice.to_json()
        for k in ['name', 'verify_key']:
            self.assertIn(k, js)
        self.assertNotIn("signing_key", js)
        alice2 = create_author_from_json(js)
        self.assertEqual(self.alice.name, alice2.name)
        self.assertEqual(self.alice.verify_key, alice2.verify_key)


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
        self.root = create_fake_tahoe_root()
        self.agent = RequestTraversalAgent(self.root)
        self.http_client = HTTPClient(
            self.agent,
            data_to_body_producer=_SynchronousProducer,
        )
        self.tahoe_client = TahoeClient(
            url=DecodedURL.from_text(u"http://example.com"),
            http_client=self.http_client,
        )
        self.alice = create_author("alice")
        self.stash_dir = self.mktemp()
        os.mkdir(self.stash_dir)

    @defer.inlineCallbacks
    def test_create_new_tahoe_snapshot(self):
        """
        create a new snapshot (this will have no parent snapshots).
        """

        data = io.BytesIO(b"test data\n" * 200)
        snapshot = yield create_snapshot(
            name="fixme_mangled_filename_or_real_path",
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
        )

        remote_snapshot = yield write_snapshot_to_tahoe(snapshot, self.tahoe_client)

        print("REMOTE: {}".format(remote_snapshot.capability))
        # XXX check signature, ...
        self.assertThat(
            remote_snapshot.name,
            Equals(snapshot.name),
        )
