import io
import os
from tempfile import mktemp
from shutil import rmtree

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

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
)

from twisted.python.filepath import (
    FilePath,
)

from allmydata.client import (
    read_config,
)

from .fixtures import (
    NodeDirectory,
)
from .common import (
    SyncTestCase,
)
from .strategies import (
    magic_folder_filenames,
)
from magic_folder.snapshot import (
    create_local_author,
    create_local_author_from_config,
    write_local_author,
    create_snapshot,
    LocalSnapshot,
)

from .. import (
    magicfolderdb,
)

class TestLocalAuthor(SyncTestCase):
    """
    Functionaltiy of LocalAuthor instances
    """

    def setUp(self):
        d = super(TestLocalAuthor, self).setUp()
        magic_dir = FilePath(mktemp())
        self.node = self.useFixture(NodeDirectory(FilePath(mktemp())))
        self.node.create_magic_folder(
            u"default",
            u"URI:CHK2:{}:{}:1:1:256".format(u"a"*16, u"a"*32),
            u"URI:CHK2:{}:{}:1:1:256".format(u"b"*16, u"b"*32),
            magic_dir,
            60,
        )

        self.config = read_config(self.node.path.path, "portnum")

        return d

    def test_serialize_author(self):
        """
        Write and then read a LocalAuthor to our node-directory
        """
        alice = create_local_author("alice")
        self.assertThat(alice.name, Equals("alice"))

        # serialize the author to disk
        write_local_author(alice, "default", self.config)

        # read back the author
        alice2 = create_local_author_from_config(self.config)
        self.assertThat(
            alice2,
            MatchesStructure(
                name=Equals("alice"),
                verify_key=Equals(alice.verify_key),
            )
        )


class TestLocalSnapshot(SyncTestCase):
    """
    Test functionality of LocalSnapshot, the in-memory version of Snapshots.
    """

    def setUp(self):
        self.alice = create_local_author("alice")
        self.stash_dir = mktemp()
        os.mkdir(self.stash_dir)

        # create a magicfolder db
        self.tempdb = FilePath(mktemp())
        self.tempdb.makedirs()
        dbfile = self.tempdb.child(u"test_snapshot.sqlite").asBytesMode().path
        self.db = magicfolderdb.get_magicfolderdb(dbfile, create_version=(magicfolderdb.SCHEMA_v1, 1))

        self.failUnless(self.db, "unable to create magicfolderdb from {}".format(dbfile))
        self.failUnlessEqual(self.db.VERSION, 1)

        return super(TestLocalSnapshot, self).setUp()

    def tearDown(self):
        rmtree(self.stash_dir)
        self.db.close()
        rmtree(self.tempdb.asBytesMode().path)
        return super(TestLocalSnapshot, self).tearDown()

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

        def get_data(snap):
            """
            So, what we really want to do here is to call
            snap.get_content_producer() and pull all the data out of
            that ... but we can't, because testtools can't work with
            a real reactor (and the only work-around I know of is
            the _SynchronousBodyProducer from treq, but we don't want
            to use that inside Snapshot because "in the real case"
            we don't want it to produce all the data synchronously)
            ...
            so, instead, we cheat a little with a test-only method
            """
            return snap._get_synchronous_content()

        self.assertThat(
            d,
            succeeded(
                AfterPreprocessing(get_data, Equals(content))
            )
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
        content1=binary(min_size=1),
        content2=binary(min_size=1),
        filename=magic_folder_filenames(),
    )
    def test_serialize_store_deserialize_snapshot(self, content1, content2, filename):
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

        self.db.store_local_snapshot(snapshots[0], filename)

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

        # serialize and store the snapshot in db.
        # It should rewrite the previously written row.
        self.db.store_local_snapshot(snapshots[1], filename)

        # now read back the serialized snapshot from db
        reconstructed_local_snapshot = self.db.get_snapshot(filename, self.alice)

        self.assertThat(
            reconstructed_local_snapshot,
            MatchesStructure(
                name=Equals(filename),
                parents_local=HasLength(1)
            )
        )

        # the initial snapshot does not have parent snapshots
        self.assertThat(
            reconstructed_local_snapshot.parents_local[0],
            MatchesStructure(
                parents_local=HasLength(0),
            )
        )

