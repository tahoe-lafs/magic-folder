from __future__ import print_function

import attr
import os

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
    lists,
    data,
)

from twisted.python.filepath import (
    FilePath,
)

from twisted.internet import defer

from testtools.matchers import (
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

    :ivar [FilePath] processed: All of the paths passed to ``process_item``,
        in the order they were passed.
    """
    processed = attr.ib(default=attr.Factory(list))

    def process_item(self, path):
        Message.log(
            message_type=u"memory-snapshot-creator:process_item",
            path=path.asTextMode("utf-8").path,
        )
        self.processed.append(path)


class LocalSnapshotServiceTests(SyncTestCase):
    """
    Tests for ``LocalSnapshotService``.
    """
    def setUp(self):
        super(LocalSnapshotServiceTests, self).setUp()
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self.snapshot_creator = MemorySnapshotCreator()
        self.snapshot_service = LocalSnapshotService(
            magic_path=self.magic_path,
            snapshot_creator=self.snapshot_creator,
        )


    @given(path_segments(), binary())
    def test_add_single_file(self, name, content):
        """
        Start the service, add a file and check if the operation succeeded.
        """
        to_add = self.magic_path.child(name)
        to_add.setContent(content)

        self.snapshot_service.startService()

        self.assertThat(
            self.snapshot_service.add_file(to_add),
            succeeded(Always()),
        )

        self.assertThat(
            self.snapshot_service.stopService(),
            succeeded(Always())
        )

        self.assertThat(
            self.snapshot_creator.processed,
            Equals([foo]),
        )

    @given(lists(path_segments().map(lambda p: p.encode("utf-8")), unique=True),
           data())
    def test_add_multiple_files(self, filenames, data):
        """
        Add a bunch of files one by one and check whether the operation is
        successful.
        """
        files = []
        for filename in filenames:
            file = self.magic_path.child(filename)
            content = data.draw(binary())
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
            sorted(self.snapshot_creator.processed),
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

class LocalSnapshotCreatorTests(SyncTestCase):
    """
    Tests for ``LocalSnapshotCreator``, responsible for creating the local
    snapshots and storing them in the database.
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
           data())
    def test_create_snapshots(self, filenames, data):
        """
        Create a list of filenames and random content as input and for each
        of the (filename, content) mapping, create and store the snapshot in
        the database.
        """
        files = []
        for filename in filenames :
            file = self.magic_path.child(filename)
            content = data.draw(binary())
            with file.open("wb") as f:
                f.write(content)
            files.append((file, content))

        for (file, _unused) in files:
            self.assertThat(
                self.snapshot_creator.process_item(file),
                succeeded(Always())
            )

        self.assertThat(self.db.get_all_localsnapshot_paths(), HasLength(len(files)))
        for (file, content) in files:
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

        # make sure the process_item() succeeds
        self.assertThat(
            self.snapshot_creator.process_item(foo),
            succeeded(Always()),
        )

        foo_magicname = path2magic(foo.asTextMode('utf-8').path)
        stored_snapshot1 = self.db.get_local_snapshot(foo_magicname, self.author)

        # now modify the file with some new content.
        with foo.open("wb") as f:
            f.write(content2)

        # make sure the second call succeeds as well
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
