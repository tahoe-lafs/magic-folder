from __future__ import (
    absolute_import,
    division,
    print_function,
)

import attr

from hypothesis import (
    assume,
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

from twisted.internet import (
    defer,
    reactor,
)

from testtools.matchers import (
    Equals,
    Always,
    HasLength,
    MatchesStructure,
    AfterPreprocessing,
    MatchesListwise,
    MatchesPredicate,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)

from eliot import (
    Message,
)

from ..magic_folder import (
    LocalSnapshotService,
    LocalSnapshotCreator,
)
from ..snapshot import (
    create_local_author,
)
from ..magicpath import (
    path2magic,
)
from ..config import (
    create_global_configuration,
    create_testing_configuration,
)
from ..status import (
    WebSocketStatusService,
)
from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
    relative_paths,
    absolute_paths,
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
            path=path.path,
        )
        self.processed.append(path)


class LocalSnapshotServiceTests(SyncTestCase):
    """
    Tests for ``LocalSnapshotService``.
    """
    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self._node_dir = FilePath(self.mktemp())
        self._node_dir.makedirs()
        self._global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            self._node_dir,
        )
        self.magic_path = FilePath(self.mktemp()).asTextMode("utf-8")
        self.magic_path.asBytesMode("utf-8").makedirs()
        self.snapshot_creator = MemorySnapshotCreator()
        self.snapshot_service = LocalSnapshotService(
            magic_path=self.magic_path,
            snapshot_creator=self.snapshot_creator,
            status=WebSocketStatusService(reactor, self._global_config),
        )


    @given(relative_paths(), binary())
    def test_add_single_file(self, relative_path, content):
        """
        Start the service, add a file and check if the operation succeeded.
        """
        to_add = self.magic_path.preauthChild(relative_path)
        to_add.asBytesMode("utf-8").parent().makedirs(ignoreExistingDirectory=True)
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
            Equals([to_add]),
        )

    @given(lists(path_segments(), unique=True),
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

    @given(relative_paths())
    def test_add_file_not_a_filepath(self, relative_path):
        """
        ``LocalSnapshotService.add_file`` returns a ``Deferred`` that fires with a
        ``Failure`` wrapping ``TypeError`` if called with something other than
        a ``FilePath``.
        """
        self.assertThat(
            self.snapshot_service.add_file(relative_path),
            failed(
                AfterPreprocessing(
                    lambda f: (f.type, f.value.args),
                    Equals((TypeError, ("argument must be a FilePath",))),
                ),
            ),
        )

    @given(relative_paths())
    def test_add_file_directory(self, relative_path):
        """
        ``LocalSnapshotService.add_file`` returns a ``Deferred`` that fires with a
        ``Failure`` wrapping ``ValueError`` if called with a path that refers
        to a directory.
        """
        to_add = self.magic_path.preauthChild(relative_path)
        to_add.asBytesMode("utf-8").makedirs()

        self.assertThat(
            self.snapshot_service.add_file(to_add),
            failed(
                AfterPreprocessing(
                    lambda f: (f.type, f.value.args),
                    MatchesListwise([
                        Equals(ValueError),
                        Equals((
                            "expected a regular file, {!r} is a directory".format(
                                to_add.path,
                            ),
                        )),
                    ]),
                ),
            ),
        )

    @given(absolute_paths())
    def test_add_file_outside_magic_directory(self, to_add):
        """
        ``LocalSnapshotService.add_file`` returns a ``Deferred`` that fires with a
        ``Failure`` wrapping ``ValueError`` if called with a path that is not
        contained by the Magic-Folder's magic directory.
        """
        assume(not to_add.startswith(self.magic_path.path))
        self.assertThat(
            self.snapshot_service.add_file(FilePath(to_add)),
            failed(
                AfterPreprocessing(
                    lambda f: (f.type, f.value.args),
                    MatchesListwise([
                        Equals(ValueError),
                        MatchesPredicate(
                            lambda args: args[0].startswith("The path being added "),
                            "%r does not start with 'The path being added '.",
                        ),
                    ]),
                ),
            ),
        )


class LocalSnapshotCreatorTests(SyncTestCase):
    """
    Tests for ``LocalSnapshotCreator``, responsible for creating the local
    snapshots and storing them in the database.
    """
    def setUp(self):
        super(LocalSnapshotCreatorTests, self).setUp()
        self.author = create_local_author(u"alice")

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self.temp = FilePath(self.mktemp()).asTextMode("utf-8")
        self.global_db = create_global_configuration(
            self.temp.child(b"global-db"),
            u"tcp:12345",
            self.temp.child(b"tahoe-node"),
            u"tcp:localhost:1234",
        )
        self.magic = self.temp.child(u"magic")
        self.magic.makedirs()
        self.db = self.global_db.create_magic_folder(
            u"some-folder",
            self.magic,
            self.author,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            60,
            0,
        )
        self.snapshot_creator = LocalSnapshotCreator(
            db=self.db,
            author=self.author,
            stash_dir=self.db.stash_path,
            magic_dir=self.db.magic_path,
            tahoe_client=None,
        )

    @given(lists(path_segments(), unique=True),
           data())
    def test_create_snapshots(self, filenames, data_strategy):
        """
        Create a list of filenames and random content as input and for each
        of the (filename, content) mapping, create and store the snapshot in
        the database.
        """
        files = []
        for filename in filenames:
            file = self.magic.child(filename)
            content = data_strategy.draw(binary())
            file.asBytesMode("utf-8").setContent(content)

            files.append((file, filename, content))

        for (file, filename, _unused) in files:
            self.assertThat(
                self.snapshot_creator.store_local_snapshot(file),
                succeeded(Always())
            )

        self.assertThat(self.db.get_all_localsnapshot_paths(), HasLength(len(files)))
        for (file, filename, content) in files:
            mangled_filename = path2magic(filename)
            stored_snapshot = self.db.get_local_snapshot(mangled_filename)
            stored_content = stored_snapshot.content_path.getContent()
            self.assertThat(stored_content, Equals(content))
            self.assertThat(stored_snapshot.parents_local, HasLength(0))

    @given(content1=binary(min_size=1),
           content2=binary(min_size=1),
           filename=path_segments(),
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

        foo_magicname = path2magic(filename)
        stored_snapshot1 = self.db.get_local_snapshot(foo_magicname)

        # now modify the file with some new content.
        foo.asBytesMode("utf-8").setContent(content2)

        # make sure the second call succeeds as well
        self.assertThat(
            self.snapshot_creator.store_local_snapshot(foo),
            succeeded(Always()),
        )
        stored_snapshot2 = self.db.get_local_snapshot(foo_magicname)

        self.assertThat(
            stored_snapshot2.parents_local[0],
            MatchesStructure(
                content_path=Equals(stored_snapshot1.content_path)
            )
        )
