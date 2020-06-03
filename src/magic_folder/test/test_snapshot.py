import io
import os
import json
from tempfile import mktemp
from shutil import rmtree
from functools import partial

from nacl.signing import (
    SigningKey,
    VerifyKey,
)

from testtools import (
    TestCase,
)
from testtools.matchers import (
    Equals,
    MatchesStructure,
    Always,
    AfterPreprocessing,
)

from testtools.twistedsupport import (
    succeeded,
)

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
    text,
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
from allmydata.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
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
from .strategies import (
    magic_folder_filenames,
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


# xxx is it 40?  who knows
MAX_LITERAL_SIZE = 40

class TahoeSnapshotTest(TestCase):
    """
    Tests for the snapshots
    """

    @defer.inlineCallbacks
    def setUp(self):
        """
        Create a Tahoe-LAFS node which contain some magic-folder configuration
        and run it.
        """
        super(TahoeSnapshotTest, self).setUp()
        self.http_client = yield create_tahoe_treq_client()
        self.tahoe_client = TahoeClient(
            url=DecodedURL.from_text(u"http://example.com"),
            http_client=self.http_client,
        )
        self.alice = create_author("alice")
        self.stash_dir = mktemp()
        os.mkdir(self.stash_dir)

    def tearDown(self):
        super(TahoeSnapshotTest, self).tearDown()
        rmtree(self.stash_dir)

    @given(
        content=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_create_new_tahoe_snapshot(self, content, filename):
        """
        create a new snapshot (this will have no parent snapshots).
        """
        data = io.BytesIO(content)

        snapshots = []
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
        )
        d.addCallback(snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        def download_content(snapshot_cap):
            d = self.tahoe_client.download_capability(snapshot_cap)
            data = json.loads(d.result)
            content_cap = data["content"][1]["ro_uri"]
            return self.tahoe_client.download_capability(content_cap)

        d = write_snapshot_to_tahoe(snapshots[0], self.tahoe_client)
        self.assertThat(
            d,
            succeeded(
                MatchesStructure(
                    # XXX check signature, ...
#                    name=Equals(snapshots[0].name),
                    capability=AfterPreprocessing(
                        download_content,
                        succeeded(Equals(data.getvalue())),
                    )
                ),
            ),
        )

        # print("REMOTE: {}".format(remote_snapshot.capability))
