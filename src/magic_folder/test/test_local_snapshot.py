from __future__ import print_function

import attr

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

from ..magic_folder import (
    LocalSnapshotService,
    SnapshotStore,
)
from ..snapshot import (
    create_local_author,
)
from ..magicpath import (
    path2magic,
)
from ..config import (
    create_global_configuration,
)
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

    :ivar [FilePath] processed: All of the paths passed to ``store_local_snapshot``,
        in the order they were passed.
    """
    processed = attr.ib(default=attr.Factory(list))

    def store_local_snapshot(self, path):
        Message.log(
            message_type=u"memory-snapshot-creator:store-local-snapshot",
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
        to_add.asBytesMode("utf-8").setContent(content)

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
            Equals([to_add.asBytesMode("utf-8")]),
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
            to_add = self.magic_path.child(filename)
            content = data.draw(binary())
            to_add.asBytesMode("utf-8").setContent(content)
            files.append(to_add)

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

    @given(path_segments(), binary())
    def test_add_file_failures(self, name, content):
        """
        Test with bad inputs to check failure paths.
        """
        to_add = self.magic_path.child(name)
        to_add.asBytesMode("utf-8").setContent(content)

        self.snapshot_service.startService()

        # try adding a string that represents the path
        with ExpectedException(TypeError,
                               "argument must be a FilePath"):
            self.snapshot_service.add_file(to_add.path)

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

class SnapshotStoreTests(SyncTestCase):
    """
    Tests for ``SnapshotStore``, responsible for creating the local
    snapshots and storing them in the database.
    """
    def setUp(self):
        super(SnapshotStoreTests, self).setUp()
        self.author = create_local_author("alice")

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self.temp = FilePath(self.mktemp())
        self.global_db = create_global_configuration(
            self.temp.child(b"global-db"),
            u"tcp:12345",
            self.temp.child(b"tahoe-node"),
        )
        self.magic = self.temp.child(b"magic")
        self.magic.makedirs()
        self.db = self.global_db.create_magic_folder(
            u"some-folder",
            self.magic,
            self.temp.child(b"state"),
            self.author,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            60,
        )
        self.snapshot_creator = SnapshotStore(
            db=self.db,
            author=self.author,
            stash_dir=self.db.stash_path,
        )

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
            file = self.magic.child(filename)
            content = data.draw(binary())
            file.asBytesMode("utf-8").setContent(content)

            files.append((file, content))

        for (file, _unused) in files:
            self.assertThat(
                self.snapshot_creator.store_local_snapshot(file),
                succeeded(Always())
            )

        self.assertThat(self.db.get_all_localsnapshot_paths(), HasLength(len(files)))
        for (file, content) in files:
            mangled_filename = path2magic(file.asTextMode(encoding="utf-8").path)
            stored_snapshot = self.db.get_local_snapshot(mangled_filename, self.author)
            stored_content = stored_snapshot.content_path.getContent()
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
        foo = self.magic.child(filename)
        foo.asBytesMode("utf-8").setContent(content1)

        # make sure the store_local_snapshot() succeeds
        self.assertThat(
            self.snapshot_creator.store_local_snapshot(foo),
            succeeded(Always()),
        )

        foo_magicname = path2magic(foo.asTextMode('utf-8').path)
        stored_snapshot1 = self.db.get_local_snapshot(foo_magicname, self.author)

        # now modify the file with some new content.
        foo.asBytesMode("utf-8").setContent(content2)

        # make sure the second call succeeds as well
        self.assertThat(
            self.snapshot_creator.store_local_snapshot(foo),
            succeeded(Always()),
        )
        stored_snapshot2 = self.db.get_local_snapshot(foo_magicname, self.author)

        self.assertThat(
            stored_snapshot2.parents_local[0],
            MatchesStructure(
                content_path=Equals(stored_snapshot1.content_path)
            )
        )
