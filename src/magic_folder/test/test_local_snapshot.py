from __future__ import print_function

import attr
import os
from tempfile import mktemp
from shutil import rmtree

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
    LocalSnapshot,
)
from magic_folder.magicpath import (
    path2magic,
)
from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
)

@attr.s
class MemoryMagicFolderDatabase(object):

    snapshots = attr.ib(default=attr.Factory(dict))

    def store_local_snapshot(self, snapshot):
        self.snapshots[snapshot.name] = (
            snapshot.metadata,
            snapshot.content_path,
            snapshot.parents_local,
        )

    def get_local_snapshot(self, name, author):
        try:
            meta, content, parents = self.snapshots[name]
        except Exception:
            return None
        return LocalSnapshot(
            name=name,
            author=author,
            metadata=meta,
            content_path=content,
            parents_local=parents,
        )

class LocalSnapshotTests(SyncTestCase):

    def setUp(self):
        super(LocalSnapshotTests, self).setUp()
        self.db = MemoryMagicFolderDatabase()
        self.author = create_local_author("alice")

        self.stash_dir = mktemp()
        os.mkdir(self.stash_dir)

        magic_path_dirname = mktemp()
        os.mkdir(magic_path_dirname)

        self.magic_path = FilePath(magic_path_dirname)
        self.snapshot_creator = LocalSnapshotCreator(
            magic_path=self.magic_path,
            db=self.db,
            author=self.author,
            stash_dir=FilePath(self.stash_dir),
        )

    def tearDown(self):
        # No need to close the db, as it is in-memory. GC
        # would claim the memory back.

        # delete stash_dir
        rmtree(self.stash_dir)

        # delete magic_path
        rmtree(self.magic_path.path)

        return super(LocalSnapshotTests, self).tearDown()

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self.db.snapshots = {}

    @given(lists(path_segments().map(lambda p: p.encode("utf-8")), unique=True),
           lists(binary(), unique=True))
    def test_add_multiple_files(self, filenames, contents):
        files = []
        for (filename, content) in zip(filenames, contents):
            file = self.magic_path.child(filename)
            with file.open("w") as f:
                f.write(content)
            files.append(file)

        self.snapshot_creator.add_files(*files)
        self.snapshot_creator.startService()
        self.assertThat(
            self.snapshot_creator.stopService(),
            succeeded(Always())
        )

        self.assertThat(self.db.snapshots.keys(), HasLength(len(files)))
        for (file, content) in zip(files, contents):
            mangled_filename = path2magic(file.asTextMode(encoding="utf-8").path)
            stored_snapshot = self.db.get_local_snapshot(mangled_filename, self.author)
            stored_content = stored_snapshot._get_synchronous_content()
            self.assertThat(stored_content, Equals(content))
            self.assertThat(stored_snapshot.parents_local, HasLength(0))


    @given(lists(path_segments().map(lambda p: p.encode("utf-8")), unique=True),
           lists(binary(), unique=True))
    def test_add_directory_with_files(self, filenames, contents):
        subdir = mktemp(dir=self.magic_path.path)
        os.mkdir(subdir)

        files = []
        for (filename, content) in zip(filenames, contents):
            file = FilePath(subdir).child(filename)
            with file.open("w") as f:
                f.write(content)
            files.append(file)

        self.snapshot_creator.add_files(FilePath(subdir))
        self.snapshot_creator.startService()
        self.assertThat(
            self.snapshot_creator.stopService(),
            succeeded(Always())
        )

        self.assertThat(self.db.snapshots.keys(), HasLength(len(files)))

    @given(content=binary())
    def test_add_non_filepath(self, content):
        foo = self.magic_path.child("foo")
        with foo.open("w") as f:
            f.write(content)

        with ExpectedException(ValueError,
                               "every argument to upload_files must be a FilePath"):
            self.snapshot_creator.add_files(foo.path)

    @given(content1=binary(min_size=1),
           content2=binary(min_size=1),
           filename=path_segments().map(lambda p: p.encode("utf-8")),
    )
    def test_add_a_file_twice(self, filename, content1, content2):
        foo = self.magic_path.child(filename)
        with foo.open("w") as f:
            f.write(content1)

        self.snapshot_creator.add_files(foo)
        self.snapshot_creator.startService()

        foo_magicname = path2magic(foo.asTextMode('utf-8').path)
        stored_snapshot1 = self.db.get_local_snapshot(foo_magicname, self.author)

        with foo.open("w") as f:
            f.write(content2)

        # it should use the previous localsnapshot as its parent.
        self.snapshot_creator.add_files(foo)
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
