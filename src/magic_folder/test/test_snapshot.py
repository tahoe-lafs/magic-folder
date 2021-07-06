from __future__ import (
    absolute_import,
    division,
    print_function,
)

import io
import json
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
    just,
    one_of,
)

from hyperlink import (
    DecodedURL,
)

from twisted.python.filepath import (
    FilePath,
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
from ..snapshot import (
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
from ..tahoe_client import (
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
        self.alice = create_local_author(u"alice")
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

        # create LocalSnapshot
        local_snapshot = success_result_of(
            create_snapshot(
                name=filename,
                author=self.alice,
                data_producer=data,
                snapshot_stash_dir=self.stash_dir,
            )
        )
        local_snapshot.parents_local = []
        local_snapshot.parents_remote = []

        # create remote snapshot
        remote_snapshot = success_result_of(
            write_snapshot_to_tahoe(local_snapshot, self.alice, self.tahoe_client)
        )

        # snapshots[1] is a RemoteSnapshot
        note("remote snapshot: {}".format(remote_snapshot))

        # now, recreate remote snapshot from the cap string and compare with the original.
        # Check whether information is preserved across these changes.

        downloaded_snapshot = success_result_of(
            create_snapshot_from_capability(remote_snapshot.capability, self.tahoe_client)
        )

        self.assertThat(downloaded_snapshot, MatchesStructure(name=Equals(filename)))
        content_io = io.BytesIO()
        success_result_of(downloaded_snapshot.fetch_content(self.tahoe_client, content_io))
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

        snapshot = success_result_of(
            create_snapshot(
                name=filename,
                author=self.alice,
                data_producer=data1,
                snapshot_stash_dir=self.stash_dir,
            )
        )
        snapshot.parents_remote = []
        snapshot.parents_local = []

        # now modify the same file and create a new local snapshot
        data2 = io.BytesIO(content2)
        child_snapshot = success_result_of(
            create_snapshot(
                name=filename,
                author=self.alice,
                data_producer=data2,
                snapshot_stash_dir=self.stash_dir,
            )
        )
        child_snapshot.parents_remote = []
        child_snapshot.parents_local = [snapshot]

        serialized = child_snapshot.to_json()

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

        # create LocalSnapshot
        local_snapshot = success_result_of(
            create_snapshot(
                name=filename,
                author=self.alice,
                data_producer=data,
                snapshot_stash_dir=self.stash_dir,
            )
        )
        local_snapshot.parents_local = []
        local_snapshot.parents_remote = []

        # snapshots[0] is a LocalSnapshot with no parents

        # turn it into a remote snapshot by uploading
        remote_snapshot = success_result_of(
            write_snapshot_to_tahoe(local_snapshot, self.alice, self.tahoe_client)
        )

        # snapshots[1] is a RemoteSnapshot with no parents,
        # corresponding to snapshots[0]

        child_local_snapshot = success_result_of(
            create_snapshot(
                name=filename,
                author=self.alice,
                data_producer=data,
                snapshot_stash_dir=self.stash_dir,
            )
        )
        child_local_snapshot.parents_local = []
        child_local_snapshot.parents_remote = [remote_snapshot.capability]

        # upload snapshots[2], turning it into a RemoteSnapshot
        # .. which should have one parent

        child_remote_snapshot = success_result_of(
            write_snapshot_to_tahoe(child_local_snapshot, self.alice, self.tahoe_client)
        )

        # ...the last thing we wrote is now a RemoteSnapshot and
        # should have a single parent
        self.assertThat(
            child_remote_snapshot,
            MatchesStructure(
                name=Equals(filename),
                parents_raw=Equals([remote_snapshot.capability]),
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

        d = self.tahoe_client.create_immutable(json.dumps(raw_metadata))
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
