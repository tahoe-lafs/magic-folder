from __future__ import print_function

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

from magic_folder.magic_folder import (
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

class LocalSnapshotTests(SyncTestCase):

    def setUp(self):
        super(LocalSnapshotTests, self).setUp()
        self.db = magicfolderdb.get_magicfolderdb(":memory:", create_version=(magicfolderdb.SCHEMA_v1, 1))
        self.author = create_local_author("alice")

        self.stash_dir = self.mktemp()
        os.mkdir(self.stash_dir)

        magic_path_dirname = self.mktemp()
        os.mkdir(magic_path_dirname)

        self.magic_path = FilePath(magic_path_dirname)
        self.snapshot_creator = LocalSnapshotCreator(
            magic_path=self.magic_path,
            db=self.db,
            author=self.author,
            stash_dir=FilePath(self.stash_dir),
        )

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self.db._clear_snapshot_table()

    @given(lists(path_segments().map(lambda p: p.encode("utf-8")), unique=True),
           lists(binary(), unique=True))
    def test_add_multiple_files(self, filenames, contents):
        files = []
        for (filename, content) in zip(filenames, contents):
            file = self.magic_path.child(filename)
            with file.open("wb") as f:
                f.write(content)
            files.append(file)

        self.snapshot_creator.startService()

        for file in files:
            self.snapshot_creator.add_file(file)

        self.assertThat(
            self.snapshot_creator.stopService(),
            succeeded(Always())
        )

        self.assertThat(self.db.get_all_localsnapshot_paths(), HasLength(len(files)))
        for (file, content) in zip(files, contents):
            mangled_filename = path2magic(file.asTextMode(encoding="utf-8").path)
            stored_snapshot = self.db.get_local_snapshot(mangled_filename, self.author)
            stored_content = stored_snapshot._get_synchronous_content()
            self.assertThat(stored_content, Equals(content))
            self.assertThat(stored_snapshot.parents_local, HasLength(0))

    @given(content=binary())
    def test_add_file_failures(self, content):
        foo = self.magic_path.child("foo")
        with foo.open("wb") as f:
            f.write(content)

        self.snapshot_creator.startService()

        # try adding a string that represents the path
        with ExpectedException(TypeError,
                               "argument must be a FilePath"):
            self.snapshot_creator.add_file(foo.path)

        # try adding a directory
        tmpdir = FilePath(self.mktemp())
        bar_dir = self.magic_path.child(tmpdir.basename())
        bar_dir.makedirs()

        with ExpectedException(ValueError,
                               "expected a file"):
            self.snapshot_creator.add_file(bar_dir)


        # try adding a file outside the magic folder directory
        tmpfile = FilePath(self.mktemp())
        with tmpfile.open("wb") as f:
            f.write(content)

        with ExpectedException(ValueError,
                               "The path being added .*"):
            self.snapshot_creator.add_file(tmpfile)

        self.assertThat(
            self.snapshot_creator.stopService(),
            succeeded(Always())
        )

    @given(content1=binary(min_size=1),
           content2=binary(min_size=1),
           filename=path_segments().map(lambda p: p.encode("utf-8")),
    )
    def test_add_a_file_twice(self, filename, content1, content2):
        foo = self.magic_path.child(filename)
        with foo.open("wb") as f:
            f.write(content1)

        self.snapshot_creator.startService()
        self.snapshot_creator.add_file(foo)

        foo_magicname = path2magic(foo.asTextMode('utf-8').path)
        stored_snapshot1 = self.db.get_local_snapshot(foo_magicname, self.author)

        with foo.open("wb") as f:
            f.write(content2)

        # it should use the previous localsnapshot as its parent.
        self.snapshot_creator.add_file(foo)
        stored_snapshot2 = self.db.get_local_snapshot(foo_magicname, self.author)

        self.assertThat(
            self.snapshot_creator.stopService(),
            succeeded(Always())
        )

        self.assertThat(stored_snapshot2.parents_local[0],
                        MatchesStructure(
                            content_path=Equals(stored_snapshot1.content_path)
                        )
        )
