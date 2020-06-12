import io
import os
import json
import base64
from tempfile import mktemp
from shutil import rmtree
from functools import partial

from nacl.signing import (
    SigningKey,
    VerifyKey,
)
from nacl.exceptions import (
    BadSignatureError,
)

from testtools import (
    TestCase,
    ExpectedException,
)
from testtools.matchers import (
    Equals,
    Raises,
    MatchesStructure,
    Always,
    AfterPreprocessing,
    StartsWith,
    IsInstance,
)

from testtools.twistedsupport import (
    succeeded,
    failed,
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
    create_tahoe_treq_client,
    create_fake_tahoe_root,
)
from allmydata.node import (
    read_config,
)

from hyperlink import (
    DecodedURL,
)

from .matchers import (
    MatchesAuthorSignature,
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
    create_local_author,
    create_local_author_from_config,
    write_local_author,
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
        self.alice = create_local_author("alice")
        return d

    def test_author_serialize(self):
        js = self.alice.to_remote_author().to_json()
        alice2 = create_author_from_json(js)

        self.assertThat(
            alice2,
            MatchesStructure(
                name=Equals(self.alice.name),
                verify_key=Equals(self.alice.verify_key),
            )
        )

    def test_author_serialize_extra_data(self):
        js = {
            "name": "wrong",
            "invalid_key": 42,
        }
        with ExpectedException(ValueError, ".*key 'invalid_key'.*"):
            create_author_from_json(js)

    def test_author_serialize_missing_data(self):
        js = {
            "name": "foo",
            # mising verify_key
        }
        with ExpectedException(ValueError, ".*requires 'verify_key'.*"):
            create_author_from_json(js)


class TahoeSnapshotTest(TestCase):
    """
    Tests for the snapshots
    """

    @defer.inlineCallbacks
    def setUp(self):
        """
        Set up a fake Tahoe client via treq, a temporary local author and
        a stash directory
        """
        super(TahoeSnapshotTest, self).setUp()
        self.root = create_fake_tahoe_root()
        self.http_client = yield create_tahoe_treq_client(self.root)
        self.tahoe_client = TahoeClient(
            url=DecodedURL.from_text(u"http://example.com"),
            http_client=self.http_client,
        )
        self.alice = create_local_author("alice")
        self.stash_dir = mktemp()
        os.mkdir(self.stash_dir)

    def tearDown(self):
        super(TahoeSnapshotTest, self).tearDown()
        rmtree(self.stash_dir)

    def _download_content(self, snapshot_cap):
        d = self.tahoe_client.download_capability(snapshot_cap)
        data = json.loads(d.result)
        content_cap = data["content"][1]["ro_uri"]
        sig = data["content"][1]["metadata"]["magic_folder"]["author_signature"]
        # XXX is it "testtools-like" to check the signature here too?
        return self.tahoe_client.download_capability(content_cap)

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

        d = write_snapshot_to_tahoe(snapshots[0], self.alice, self.tahoe_client)
        self.assertThat(
            d,
            succeeded(
                MatchesStructure(
                    name=Equals(snapshots[0].name),
                    capability=AfterPreprocessing(
                        self._download_content,
                        succeeded(Equals(data.getvalue())),
                    ),
                    signature=MatchesAuthorSignature(snapshots[0], d.result),
                ),
            ),
        )

    @given(
        content1=binary(min_size=1),
        content2=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_create_local_snapshots(self, content1, content2, filename):
        """
        Create a local snapshot and then change the content of the file
        to make another snapshot.
        """
        data1 = io.BytesIO(content1)
        parents = []

        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data1,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
        )
        d.addCallback(parents.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        data2 = io.BytesIO(content2)
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data2,
            snapshot_stash_dir=self.stash_dir,
            parents=parents,
        )
        d.addCallback(parents.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        d = write_snapshot_to_tahoe(parents[1], self.alice, self.tahoe_client)
        self.assertThat(
            d,
            succeeded(
                MatchesStructure(
                    # XXX check signature, ...
#                    name=Equals(snapshots[0].name),
                    capability=AfterPreprocessing(
                        self._download_content,
                        succeeded(Equals(data2.getvalue())),
                    )
                ),
            ),
        )


    @given(
        content=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_snapshot_roundtrip(self, content, filename):
        """
        Create a local snapshot, write into tahoe to create a remote snapshot,
        then read back the data from the snapshot cap to recreate the remote
        snapshot and check if it is the same as the previous one.
        """
        data = io.BytesIO(content)

        snapshots = []
        # create LocalSnapshot
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

        # create remote snapshot
        d = write_snapshot_to_tahoe(snapshots[0], self.alice, self.tahoe_client)
        d.addCallback(snapshots.append)

        # snapshots[1] is a RemoteSnapshot
        # print("remote snapshot: {}".format(snapshots[1]))

        # now, recreate remote snapshot from the cap string and compare with the original.
        # Check whether information is preserved across these changes.

        snapshot_d = create_snapshot_from_capability(snapshots[1].capability, self.tahoe_client)
        self.assertThat(snapshot_d, succeeded(Always()))
        snapshot = snapshot_d.result

        self.assertThat(snapshot, MatchesStructure(name=Equals(filename)))
        content_io = io.BytesIO()
        snapshot.fetch_content(self.tahoe_client, content_io)
        self.assertEqual(content_io.getvalue(), content)

    @given(
        content1=binary(min_size=1),
        content2=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_snapshots_with_parents(self, content1, content2, filename):
        """
        Create a local snapshot, commit it to the grid, then extend that
        with another local snapshot and again commit it with the previously
        created remote snapshot as the parent. Now, fetch the remote from the
        capability string and compare parent to see if they match.
        """
        data1 = io.BytesIO(content1)
        local_snapshots = []
        remote_snapshots = []

        # create a local snapshot and commit it to the grid
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data1,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
        )
        d.addCallback(local_snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        # commit to grid
        d = write_snapshot_to_tahoe(local_snapshots[0], self.alice, self.tahoe_client)
        d.addCallback(remote_snapshots.append)

        # now modify the same file and create a new local snapshot
        # with the last committed remote as parent
        data2 = io.BytesIO(content2)
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data2,
            snapshot_stash_dir=self.stash_dir,
            parents=remote_snapshots,
        )

        d.addCallback(local_snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        d = write_snapshot_to_tahoe(local_snapshots[1], self.alice, self.tahoe_client)
        d.addCallback(remote_snapshots.append)

        # now if we fetch the tip remote snapshot, it should have the previous
        # remote snapshot as its parent

        parents_matcher = MatchesStructure(parents_raw=Equals([remote_snapshots[0].capability]))
        self.assertThat(
            create_snapshot_from_capability(remote_snapshots[1].capability, self.tahoe_client),
            succeeded(
                parents_matcher
            )
        )

    def test_snapshot_invalid_signature(self):
        """
        Hand-create a snapshot in the grid with an invalid signature,
        verifying that we fail to read this snapshot out of the grid.
        """
        content = (b"fake content\n" * 20)
        #content_cap = yield self.tahoe_client.create_immutable(content)
        content_cap = self.root.add_data("URI:CHK:", content)

        author_cap = self.root.add_data(
            "URI:CHK:",
            json.dumps(self.alice.to_remote_author().to_json())
        )

        bad_sig = base64.b64encode(b"0" * 32)

        # create remote snapshot, but with a bogus signature
        data = {
            "content": [
                "filenode", {
                    "ro_uri": content_cap,
                    "metadata": {
                        "magic_folder": {
                            "snapshot_version": 1,
                            "name": "a_file",
                            "author_signature": bad_sig,
                        }
                    },
                },
            ],
            "author": [
                "filenode", {
                    "ro_uri": author_cap,
                    "metadata": {
                        "ctime": 1202777696.7564139,
                        "mtime": 1202777696.7564139,
                        "tahoe": {
                            "linkcrtime": 1202777696.7564139,
                            "linkmotime": 1202777696.7564139
                        }
                    }
                }
            ],
        }
        snapshot_cap = self.root.add_data("URI:DIR2-CHK:", json.dumps(data))

        snapshot_d = create_snapshot_from_capability(snapshot_cap, self.tahoe_client)
        self.assertThat(
            snapshot_d,
            failed(
                AfterPreprocessing(
                    lambda f: f.value,
                    IsInstance(BadSignatureError)
                )
            )
        )

    def test_serialize_snapshot_author(self):
        """
        Write and then read a LocalAuthor to our node-directory
        """
        magic_dir = FilePath(mktemp())
        node = self.useFixture(NodeDirectory(FilePath(mktemp())))
        node.create_magic_folder(
            u"default",
            u"URI:CHK2:{}:{}:1:1:256".format(u"a"*16, u"a"*32),
            u"URI:CHK2:{}:{}:1:1:256".format(u"b"*16, u"b"*32),
            magic_dir,
            60,
        )

        config = read_config(node.path.path, "portnum")
        author = create_local_author("bob")
        write_local_author(author, "default", config)

        # read back the author
        bob = create_local_author_from_config(config)
        self.assertThat(
            bob,
            MatchesStructure(
                name=Equals("bob"),
                verify_key=Equals(author.verify_key),
            )
        )
