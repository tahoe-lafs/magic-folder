from __future__ import print_function

import attr
import os

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
    lists,
)

from twisted.python.filepath import (
    FilePath,
)

from twisted.internet import defer

from testtools.matchers import (
    Not,
    Equals,
    Always,
    HasLength,
    MatchesStructure,
)
from testtools.twistedsupport import (
    succeeded,
)
from testtools import (
    ExpectedException,
)

from eliot import (
    Message,
)

from magic_folder.magic_folder import (
    LocalSnapshotService,
    LocalSnapshotCreator,
)
from magic_folder.snapshot import (
    create_local_author,
)
from magic_folder.magicpath import (
    path2magic,
)
from .. import magicfolderdb

from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
)

@attr.s
class MemorySnapshotCreator(object):
    """
    A way to test LocalSnapshotService with an in-memory database.
    """
    processed = attr.ib(default=attr.Factory(dict))
    def process_item(self, path):
        Message.log(
            message_type=u"memory-snapshot-creator:process_item",
            path=path.asTextMode("utf-8").path,
        )
        if path not in self.processed.keys():
            # record number of parents
            self.processed[path] = 0
        else:
            num_parents = self.processed[path]
            self.processed[path] = num_parents + 1
        return defer.succeed(None)


class LocalSnapshotServiceTests(SyncTestCase):

    def setUp(self):
        super(LocalSnapshotServiceTests, self).setUp()
        self.db = magicfolderdb.get_magicfolderdb(":memory:", create_version=(magicfolderdb.SCHEMA_v1, 1))
        self.author = create_local_author("alice")

        self.stash_dir = FilePath(self.mktemp())
        self.stash_dir.makedirs()

        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()

        self._snapshot_creator = MemorySnapshotCreator()

        self.snapshot_service = LocalSnapshotService(
            magic_path=self.magic_path,
            snapshot_creator=self._snapshot_creator,
        )

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self._snapshot_creator.processed = {}

    def test_add_single_file(self):
        """
        Start the service, add a file and check if the operation succeeded.
        """
        foo = self.magic_path.child("foo")
        content = u"foo"
        with foo.open("w") as f:
            f.write(content)

        self.snapshot_service.startService()
        d = self.snapshot_service.add_file(foo)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        self.assertThat(
            self.snapshot_service.stopService(),
            succeeded(Always())
        )

        self.assertThat(self._snapshot_creator.processed.get(foo), Equals(0))

    @given(lists(path_segments().map(lambda p: p.encode("utf-8")), unique=True),
           lists(binary(), unique=True))
    def test_add_multiple_files(self, filenames, contents):
        """
        Add a bunch of files one by one and check whether the operation is
        successful.
        """
        files = []
        for (filename, content) in zip(filenames, contents):
            file = self.magic_path.child(filename)
            with file.open("wb") as f:
                f.write(content)
            files.append(file)

        self.snapshot_service.startService()

        list_d = []
        for file in files:
            result_d = self.snapshot_service.add_file(file)
            list_d.append(result_d)

        d = defer.gatherResults(list_d)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        self.assertThat(
            self.snapshot_service.stopService(),
            succeeded(Always())
        )

        self.assertThat(
            len(self._snapshot_creator.processed.keys()),
            Equals(len(files))
        )

        self.assertThat(
            sorted(self._snapshot_creator.processed.keys()),
            Equals(sorted(files))
        )

    @given(content=binary())
    def test_add_file_failures(self, content):
        """
        Test with bad inputs to check failure paths.
        """
        foo = self.magic_path.child("foo")
        with foo.open("wb") as f:
            f.write(content)

        self.snapshot_service.startService()

        # try adding a string that represents the path
        with ExpectedException(TypeError,
                               "argument must be a FilePath"):
            self.snapshot_service.add_file(foo.path)

        # try adding a directory
        tmpdir = FilePath(self.mktemp())
        bar_dir = self.magic_path.child(tmpdir.basename())
        bar_dir.makedirs()

        with ExpectedException(ValueError,
                               "expected a file"):
            self.snapshot_service.add_file(bar_dir)


        # try adding a file outside the magic folder directory
        tmpfile = FilePath(self.mktemp())
        with tmpfile.open("wb") as f:
            f.write(content)

        with ExpectedException(ValueError,
                               "The path being added .*"):
            self.snapshot_service.add_file(tmpfile)

        self.assertThat(
            self.snapshot_service.stopService(),
            succeeded(Always())
        )

    @given(content1=binary(min_size=1),
           content2=binary(min_size=1),
           filename=path_segments().map(lambda p: p.encode("utf-8")),
    )
    def test_add_a_file_twice(self, filename, content1, content2):
        """
        Adding a file twice should create two local snapshots with the first
        one being the parent of the second.
        """
        foo = self.magic_path.child(filename)
        with foo.open("wb") as f:
            f.write(content1)

        self.snapshot_service.startService()
        self.snapshot_service.add_file(foo)

        with foo.open("wb") as f:
            f.write(content2)

        # it should use the previous localsnapshot as its parent.
        self.snapshot_service.add_file(foo)

        self.assertThat(
            self.snapshot_service.stopService(),
            succeeded(Always())
        )

        self.assertThat(self._snapshot_creator.processed.get(foo),
                        Equals(1)
        )

class LocalSnapshotCreatorTests(SyncTestCase):
    """
    Tests for LocalSnapshotCreator that is responsible for
    creating the local snapshots and storing them in the database.
    """
    def setUp(self):
        # setup db
        # create author
        # setup stashdir
        # setup magicpath dir (the base directory for a particular magic folder)
        # instantiate LocalSnapshotCreator
        super(LocalSnapshotCreatorTests, self).setUp()
        self.db = magicfolderdb.get_magicfolderdb(":memory:", create_version=(magicfolderdb.SCHEMA_v1, 1))
        self.author = create_local_author("alice")

        self.stash_dir = self.mktemp()
        os.mkdir(self.stash_dir)

        magic_path_dirname = self.mktemp()
        os.mkdir(magic_path_dirname)
        self.magic_path = FilePath(magic_path_dirname)

        self.snapshot_creator = LocalSnapshotCreator(
            db=self.db,
            author=self.author,
            stash_dir=FilePath(self.stash_dir),
        )

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        # XXX: Not sure if this function should exist at all and even if
        # so, it shouldn't exist in magicfolderdb module perhaps?
        self.db._clear_snapshot_table()

    @given(lists(path_segments().map(lambda p: p.encode("utf-8")), unique=True),
           lists(binary(), unique=True))
    def test_create_snapshots(self, filenames, contents):
        """
        Create a list of filenames and random content as input and for each
        of the (filename, content) mapping, create and store the snapshot in
        the database.
        """
        files = []
        for (filename, content) in zip(filenames, contents):
            file = self.magic_path.child(filename)
            with file.open("wb") as f:
                f.write(content)
            files.append(file)

        for file in files:
            self.assertThat(
                self.snapshot_creator.process_item(file),
                succeeded(Always())
            )

        self.assertThat(self.db.get_all_localsnapshot_paths(), HasLength(len(files)))
        for (file, content) in zip(files, contents):
            mangled_filename = path2magic(file.asTextMode(encoding="utf-8").path)
            stored_snapshot = self.db.get_local_snapshot(mangled_filename, self.author)
            stored_content = stored_snapshot._get_synchronous_content()
            self.assertThat(stored_content, Equals(content))
            self.assertThat(stored_snapshot.parents_local, HasLength(0))

    @given(content1=binary(min_size=1),
           content2=binary(min_size=1),
           filename=path_segments().map(lambda p: p.encode("utf-8")),
    )
    def test_create_snapshot_twice(self, filename, content1, content2):
        """
        If a snapshot already exists for a file, adding a new snapshot to it
        should refer to the existing snapshot as a parent.
        """
        foo = self.magic_path.child(filename)
        with foo.open("wb") as f:
            f.write(content1)

        self.assertThat(
            self.snapshot_creator.process_item(foo),
            succeeded(Always()),
        )

        foo_magicname = path2magic(foo.asTextMode('utf-8').path)
        stored_snapshot1 = self.db.get_local_snapshot(foo_magicname, self.author)

        # now modify the file with some new content.
        with foo.open("wb") as f:
            f.write(content2)

        self.assertThat(
            self.snapshot_creator.process_item(foo),
            succeeded(Always()),
        )
        stored_snapshot2 = self.db.get_local_snapshot(foo_magicname, self.author)

        self.assertThat(
            stored_snapshot2.parents_local[0],
            MatchesStructure(
                content_path=Equals(stored_snapshot1.content_path)
            )
        )
