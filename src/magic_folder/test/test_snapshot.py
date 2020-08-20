import io
from tempfile import mktemp

from testtools.matchers import (
    Equals,
    Contains,
    MatchesStructure,
    AfterPreprocessing,
    Always,
    HasLength,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)
from testtools import (
    ExpectedException,
)

from hypothesis import (
    assume,
    given,
    note,
)
from hypothesis.strategies import (
    binary,
    text,
)

from hyperlink import (
    DecodedURL,
)

from twisted.python.filepath import (
    FilePath,
)

from allmydata.client import (
    read_config,
)
# After a Tahoe 1.15.0 or higher release, these should be imported
# from Tahoe instead
from magic_folder.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)

from .fixtures import (
    NodeDirectory,
)
from .common import (
    SyncTestCase,
)
from .strategies import (
    magic_folder_filenames,
    remote_authors,
    author_names,
)
from magic_folder.snapshot import (
    create_local_author,
    create_author_from_json,
    create_author,
    create_snapshot,
    create_snapshot_from_capability,
    write_snapshot_to_tahoe,
    LocalSnapshot,
    UnknownPropertyError,
    MissingPropertyError,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)

class TestRemoteAuthor(SyncTestCase):
    """
    Tests for RemoteAuthor and the related constructors, ``create_author`` and
    ``create_author_from_json``.
    """
    @given(remote_authors())
    def test_json_roundtrip(self, remote_author):
        """
        create_author_from_json . RemoteAuthor.to_json = id
        """
        self.assertThat(
            create_author_from_json(remote_author.to_json()),
            Equals(remote_author),
        )

    @given(remote_authors())
    def test_from_json_missing_property(self, author):
        """
        If the JSON input to create_author_from_json is missing any of the
        properties emitted by RemoteAuthor.to_json then it raises
        ``ValueError``.
        """
        js = author.to_json()
        missing = js.popitem()[0]
        with ExpectedException(MissingPropertyError, missing):
            create_author_from_json(js)

    @given(remote_authors(), text(), text())
    def test_author_serialize_extra_data(self, remote_author, extra_key, extra_value):
        """
        If the JSON input to create_author_from_json has any extra properties
        beyond those emitted by RemoteAuthor.to_json then it raises
        ``ValueError``.
        """
        js = remote_author.to_json()
        assume(extra_key not in js)
        js[extra_key] = extra_value
        with ExpectedException(UnknownPropertyError):
            create_author_from_json(js)

    @given(author_names())
    def test_author_create_wrong_key(self, name):
        """
        create_author raises TypeError if passed a value for verify_key which is
        not an instance of VerifyKey.
        """
        with ExpectedException(TypeError, ".*not a VerifyKey.*"):
            create_author(name, "not a VerifyKey")


class TestLocalSnapshot(SyncTestCase):
    """
    Test functionality of LocalSnapshot, the representation of non-uploaded
    snapshots.
    """
    def setUp(self):
        super(TestLocalSnapshot, self).setUp()
        self.alice = create_local_author("alice")

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        """
        self.stash_dir = FilePath(self.mktemp())
        self.stash_dir.makedirs()

    @given(
        content=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_create_new_snapshot(self, content, filename):
        """
        create a new snapshot (this will have no parent snapshots).
        """
        data = io.BytesIO(content)

        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
        )

        self.assertThat(
            d,
            succeeded(
                AfterPreprocessing(
                    lambda snapshot: snapshot.content_path.getContent(),
                    Equals(content),
                ),
            ),
        )

    @given(
        content=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_snapshot_improper_parent(self, content, filename):
        """
        a snapshot with non-LocalSnapshot parents fails
        """
        data = io.BytesIO(content)

        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=["not a LocalSnapshot instance"],
        )

        self.assertThat(
            d,
            failed(
                AfterPreprocessing(
                    str,
                    Contains("Parent 0 is type <type 'str'> not LocalSnapshot")
                )
            )
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

        # now modify the same file and create a new local snapshot
        data2 = io.BytesIO(content2)
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data2,
            snapshot_stash_dir=self.stash_dir,
            parents=local_snapshots,
        )

        d.addCallback(local_snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )


class TestRemoteSnapshot(SyncTestCase):
    """
    Test upload and download of LocalSnapshot (creating RemoteSnapshot)
    """
    def setup_example(self):
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com"),
            self.http_client,
        )
        self.alice = create_local_author("alice")
        self.stash_dir = FilePath(mktemp())
        self.stash_dir.makedirs()  # 'trial' will delete this when done

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

        self.assertThat(
            d,
            succeeded(Always()),
        )

        # snapshots[1] is a RemoteSnapshot
        note("remote snapshot: {}".format(snapshots[1]))

        # now, recreate remote snapshot from the cap string and compare with the original.
        # Check whether information is preserved across these changes.

        snapshot_d = create_snapshot_from_capability(snapshots[1].capability, self.tahoe_client)
        snapshot_d.addCallback(snapshots.append)
        self.assertThat(snapshot_d, succeeded(Always()))
        snapshot = snapshots[-1]

        self.assertThat(snapshot, MatchesStructure(name=Equals(filename)))
        content_io = io.BytesIO()
        self.assertThat(
            snapshot.fetch_content(self.tahoe_client, content_io),
            succeeded(Always()),
        )
        self.assertEqual(content_io.getvalue(), content)

    @given(
        content1=binary(min_size=1),
        content2=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_serialize_deserialize_snapshot(self, content1, content2, filename):
        """
        create a new snapshot (this will have no parent snapshots).
        """
        data1 = io.BytesIO(content1)

        snapshots = []
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data1,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
        )
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        # now modify the same file and create a new local snapshot
        data2 = io.BytesIO(content2)
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data2,
            snapshot_stash_dir=self.stash_dir,
            parents=[snapshots[0]],
        )
        d.addCallback(snapshots.append)

        serialized = snapshots[1].to_json()

        reconstructed_local_snapshot = LocalSnapshot.from_json(serialized, self.alice)

        self.assertThat(
            reconstructed_local_snapshot,
            MatchesStructure(
                name=Equals(filename),
                parents_local=HasLength(1),
            )
        )

    @given(
        content=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_snapshot_remote_parent(self, content, filename):
        """
        Create a local snapshot, write into tahoe to create a remote
        snapshot, then create another local snapshot with a remote
        parent. This local snapshot retains its parent when converted
        to a remote.
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

        # snapshots[0] is a LocalSnapshot with no parents

        # turn it into a remote snapshot by uploading
        d = write_snapshot_to_tahoe(snapshots[0], self.alice, self.tahoe_client)
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        # snapshots[1] is a RemoteSnapshot with no parents,
        # corresponding to snapshots[0]

        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[snapshots[1]],
        )
        d.addCallback(snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )
        self.assertThat(
            snapshots[2],
            MatchesStructure(
                name=Equals(filename),
                parents_remote=AfterPreprocessing(len, Equals(1)),
            )
        )

        # upload snapshots[2], turning it into a RemoteSnapshot
        # .. which should have one parent

        d = write_snapshot_to_tahoe(snapshots[2], self.alice, self.tahoe_client)
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )
        # ...the last thing we wrote is now a RemoteSnapshot and
        # should have a single parent
        self.assertThat(
            snapshots[3],
            MatchesStructure(
                name=Equals(filename),
                parents_raw=Equals([snapshots[1].capability]),
            )
        )

    @given(
        content=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_snapshot_local_parent(self, content, filename):
        """
        Create a local snapshot and then another local snapshot with the
        first as parent. Then upload both at once.
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

        # snapshots[0] is a LocalSnapshot with no parents

        # create another LocalSnapshot with the first as parent
        d = create_snapshot(
            name=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[snapshots[0]],
        )
        d.addCallback(snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        # turn them both into RemoteSnapshots
        d = write_snapshot_to_tahoe(snapshots[1], self.alice, self.tahoe_client)
        d.addCallback(snapshots.append)
        self.assertThat(d, succeeded(Always()))

        # ...the last thing we wrote is now a RemoteSnapshot and
        # should have a single parent.
        self.assertThat(
            snapshots[2],
            MatchesStructure(
                name=Equals(filename),
                parents_raw=AfterPreprocessing(len, Equals(1)),
            )
        )

        # turn the parent into a RemoteSnapshot
        d = snapshots[2].fetch_parent(self.tahoe_client, 0)
        d.addCallback(snapshots.append)
        self.assertThat(d, succeeded(Always()))
        self.assertThat(
            snapshots[3],
            MatchesStructure(
                name=Equals(filename),
                parents_raw=Equals([]),
            )
        )
