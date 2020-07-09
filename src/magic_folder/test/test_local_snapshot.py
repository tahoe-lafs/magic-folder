from __future__ import print_function

import attr

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
)

from twisted.internet import (
    task,
)
from twisted.python.filepath import (
    FilePath,
)

from testtools.matchers import (
    Equals,
    Always,
    HasLength,
)
from testtools.twistedsupport import (
    succeeded,
)

from magic_folder.magic_folder import (
    LocalSnapshotCreator,
)
from magic_folder.snapshot import (
    create_local_author,
    LocalSnapshot,
)

from .common import (
    AsyncTestCase,
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
        meta, content, parents = self.snapshots[name]  # may raise
        return LocalSnapshot(
            name=name,
            author=author,
            metadata=meta,
            content_path=content,
            parents_local=parents,
        )


class LocalSnapshotTests(AsyncTestCase):

    def setUp(self):
        super(LocalSnapshotTests, self).setUp()
        self.db = MemoryMagicFolderDatabase()
        self.clock = task.Clock()
        self.author = create_local_author("alice")
        self.stash_dir = FilePath("/tmp")  # XXX FIXME
        self.magic_path = FilePath("/tmp")  # XXX FIXME
        self.snapshot_creator = LocalSnapshotCreator(
            magic_path=self.magic_path,
            db=self.db,
            clock=self.clock,
            author=self.author,
            stash_dir=self.stash_dir,
        )

    @given(content=binary())
    def test_add_single_file(self, content):
        foo = self.magic_path.child("foo")
        with foo.open("w") as f:
            f.write(content)

        self.snapshot_creator.add_files(foo)
        self.snapshot_creator.startService()
        self.assertThat(
            self.snapshot_creator.stopService(),
            succeeded(Always())
        )

        # we should have processed one snapshot upload

        self.assertThat(self.db.snapshots, HasLength(1))
        stored_snapshot = self.db.get_local_snapshot("@_tmp@_foo", self.author)
        stored_content = stored_snapshot._get_synchronous_content()
        self.assertThat(stored_content, Equals(content))
        self.assertThat(stored_snapshot.parents_local, HasLength(0))
        # self.assertThat(stored_snapshot.parents_remote, HasLength(0))
