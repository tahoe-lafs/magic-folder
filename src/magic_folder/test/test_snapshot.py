import io
import json
from tempfile import mktemp

from testtools.matchers import (
    Equals,
    Contains,
    ContainsDict,
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
    just,
    one_of,
    integers,
)

from hyperlink import (
    DecodedURL,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    Cooperator,
)

# After a Tahoe 1.15.0 or higher release, these should be imported
# from Tahoe instead
from magic_folder.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)

from .common import (
    SyncTestCase,
    success_result_of,
)
from .strategies import (
    magic_folder_filenames,
    remote_authors,
    author_names,
)
from ..util.capabilities import (
    Capability,
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
    format_filenode,
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
        self.alice = create_local_author(u"alice")
        self.uncooperator = Cooperator(
            terminationPredicateFactory=lambda: lambda: False,
            scheduler=lambda f: f(),
        )
        self.addCleanup(self.uncooperator.stop)

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
            relpath=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
            cooperator=self.uncooperator,
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
            relpath=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=["not a LocalSnapshot instance"],
            cooperator=self.uncooperator,
        )

        self.assertThat(
            d,
            failed(
                AfterPreprocessing(
                    str,
                    Contains("Parent 0 is type <class 'str'> not LocalSnapshot")
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
            relpath=filename,
            author=self.alice,
            data_producer=data1,
            snapshot_stash_dir=self.stash_dir,
            cooperator=self.uncooperator,
        )
        d.addCallback(parents.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        data2 = io.BytesIO(content2)
        d = create_snapshot(
            relpath=filename,
            author=self.alice,
            data_producer=data2,
            snapshot_stash_dir=self.stash_dir,
            parents=parents,
            cooperator=self.uncooperator,
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
            relpath=filename,
            author=self.alice,
            data_producer=data1,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
            cooperator=self.uncooperator,
        )
        d.addCallback(local_snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )

        # now modify the same file and create a new local snapshot
        data2 = io.BytesIO(content2)
        d = create_snapshot(
            relpath=filename,
            author=self.alice,
            data_producer=data2,
            snapshot_stash_dir=self.stash_dir,
            parents=local_snapshots,
            cooperator=self.uncooperator,
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

    def setUp(self):
        self.uncooperator = Cooperator(
            terminationPredicateFactory=lambda: lambda: False,
            scheduler=lambda f: f(),
        )
        self.addCleanup(self.uncooperator.stop)
        return super(TestRemoteSnapshot, self).setUp()

    def setup_example(self):
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com"),
            self.http_client,
        )
        self.alice = create_local_author(u"alice")
        self.stash_dir = FilePath(mktemp())
        self.stash_dir.makedirs()  # 'trial' will delete this when done

    @given(
        content=binary(min_size=1),
        filename=magic_folder_filenames(),
        modified_time=integers(min_value=0),
    )
    def test_snapshot_roundtrip(self, content, filename, modified_time):
        """
        Create a local snapshot, write into tahoe to create a remote snapshot,
        then read back the data from the snapshot cap to recreate the remote
        snapshot and check if it is the same as the previous one.
        """
        data = io.BytesIO(content)

        # create LocalSnapshot
        local_snapshot = success_result_of(
            create_snapshot(
                relpath=filename,
                author=self.alice,
                data_producer=data,
                snapshot_stash_dir=self.stash_dir,
                parents=[],
                modified_time=modified_time,
                cooperator=self.uncooperator,
            )
        )

        # create remote snapshot
        remote_snapshot = success_result_of(
            write_snapshot_to_tahoe(local_snapshot, self.alice, self.tahoe_client)
        )

        note("remote snapshot: {}".format(remote_snapshot))

        # now, recreate remote snapshot from the cap string and compare with the original.
        # Check whether information is preserved across these changes.

        downloaded_snapshot = success_result_of(
            create_snapshot_from_capability(remote_snapshot.capability, self.tahoe_client)
        )

        self.assertThat(
            downloaded_snapshot,
            MatchesStructure(
                metadata=ContainsDict({
                    "relpath": Equals(filename),
                    "modification_time": Equals(modified_time),
                }),
            ),
        )
        content_io = io.BytesIO()
        self.assertThat(
            downloaded_snapshot.fetch_content(self.tahoe_client, content_io),
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

        local_snapshot = success_result_of(
            create_snapshot(
                relpath=filename,
                author=self.alice,
                data_producer=data1,
                snapshot_stash_dir=self.stash_dir,
                parents=[],
                cooperator=self.uncooperator,
            )
        )

        # now modify the same file and create a new local snapshot
        data2 = io.BytesIO(content2)
        child_snapshot = success_result_of(
            create_snapshot(
                relpath=filename,
                author=self.alice,
                data_producer=data2,
                snapshot_stash_dir=self.stash_dir,
                parents=[local_snapshot],
                cooperator=self.uncooperator,
            )
        )

        serialized = child_snapshot.to_json()

        reconstructed_local_snapshot = LocalSnapshot.from_json(serialized, self.alice)

        self.assertThat(
            reconstructed_local_snapshot,
            MatchesStructure(
                relpath=Equals(filename),
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
            relpath=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[],
            cooperator=self.uncooperator,
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
            relpath=filename,
            author=self.alice,
            data_producer=data,
            snapshot_stash_dir=self.stash_dir,
            parents=[snapshots[1]],
            cooperator=self.uncooperator,
        )
        d.addCallback(snapshots.append)
        self.assertThat(
            d,
            succeeded(Always()),
        )
        self.assertThat(
            snapshots[2],
            MatchesStructure(
                relpath=Equals(filename),
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
                metadata=ContainsDict({"relpath": Equals(filename)}),
                parents_raw=Equals([snapshots[1].capability.danger_real_capability_string()]),
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

        # create LocalSnapshot
        local_snapshot = success_result_of(
            create_snapshot(
                relpath=filename,
                author=self.alice,
                data_producer=data,
                snapshot_stash_dir=self.stash_dir,
                parents=[],
                cooperator=self.uncooperator,
            )
        )

        # create another LocalSnapshot with the first as parent
        child_snapshot = success_result_of(
            create_snapshot(
                relpath=filename,
                author=self.alice,
                data_producer=data,
                snapshot_stash_dir=self.stash_dir,
                parents=[local_snapshot],
                cooperator=self.uncooperator,
            )
        )

        # turn them both into RemoteSnapshots
        remote_snapshot = success_result_of(
            write_snapshot_to_tahoe(child_snapshot, self.alice, self.tahoe_client)
        )

        # ...the last thing we wrote is now a RemoteSnapshot and
        # should have a single parent.
        self.assertThat(
            remote_snapshot,
            MatchesStructure(
                metadata=ContainsDict({"relpath": Equals(filename)}),
                parents_raw=AfterPreprocessing(len, Equals(1)),
            )
        )

        # turn the parent into a RemoteSnapshot
        parent_snapshot = success_result_of(
            create_snapshot_from_capability(
                Capability.from_string(remote_snapshot.parents_raw[0]),
                self.tahoe_client,
            )
        )
        self.assertThat(
            parent_snapshot,
            MatchesStructure(
                metadata=ContainsDict({"relpath": Equals(filename)}),
                parents_raw=Equals([]),
            )
        )

    @given(
        one_of(
            just({}),
            just({"snapshot_version": 2**31 - 1}),
            just({"snapshot_version": "foo"}),
        )
    )
    def test_snapshot_bad_metadata(self, raw_metadata):
        """
        Test error-handling cases when de-serializing a snapshot. If the
        snapshot version is missing or wrong we should error.
        """

        # arbitrary (but valid) content-cap
        contents = []
        content_cap_d = self.tahoe_client.create_immutable(b"0" * 256)
        content_cap_d.addCallback(contents.append)
        self.assertThat(content_cap_d, succeeded(Always()))
        content_cap = contents[0]

        # invalid metadata cap (we use Hypothesis to give us two
        # definitely-invalid versions)
        metadata_caps = []

        d = self.tahoe_client.create_immutable(json.dumps(raw_metadata).encode("utf8"))
        d.addCallback(metadata_caps.append)
        self.assertThat(d, succeeded(Always()))

        # create a Snapshot using the wrong metadata
        raw_snapshot_data = {
            u"content": format_filenode(content_cap),
            u"metadata": format_filenode(
                metadata_caps[0], {
                    u"magic_folder": {
                        u"author_signature": u"not valid",
                    },
                },
            ),
        }

        snapshot_cap = []
        d = self.tahoe_client.create_immutable_directory(raw_snapshot_data)
        d.addCallback(snapshot_cap.append)
        self.assertThat(d, succeeded(Always()))

        # now when we read back the snapshot with incorrect metadata,
        # it should fail
        snapshot_d = create_snapshot_from_capability(snapshot_cap[0], self.tahoe_client)

        self.assertThat(snapshot_d, failed(
            MatchesStructure(
                value=AfterPreprocessing(str, Contains("snapshot_version")),
            )
        ))
