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
from twisted.internet.task import (
    Cooperator,
)

from twisted.internet import (
    defer,
    reactor,
)

from testtools.matchers import (
    AfterPreprocessing,
    Always,
    Equals,
    HasLength,
    MatchesStructure,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)

from eliot import (
    Message,
)

from ..common import APIError
from ..magic_folder import (
    LocalSnapshotService,
    LocalSnapshotCreator,
)
from ..snapshot import (
    create_local_author,
)
from ..config import (
    create_global_configuration,
    create_testing_configuration,
)
from ..status import (
    FolderStatus,
    WebSocketStatusService,
)
from ..util.file import (
    seconds_to_ns,
)
from ..util.capabilities import (
    random_immutable,
    random_dircap,
)
from .common import (
    SyncTestCase,
)
from .matchers import matches_failure
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

    def store_local_snapshot(self, path, local_snapshot=None):
        Message.log(
            message_type=u"memory-snapshot-creator:store-local-snapshot",
            path=path.path,
            local_snapshot=local_snapshot,
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
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self.magic_config = self._global_config.create_magic_folder(
            "name",
            self.magic_path,
            create_local_author("author"),
            random_immutable(directory=True),
            random_dircap(),
            60,
            None,
        )

        self.status = WebSocketStatusService(reactor, self._global_config)
        self.snapshot_creator = MemorySnapshotCreator()
        self.snapshot_service = LocalSnapshotService(
            config=self.magic_config,
            snapshot_creator=self.snapshot_creator,
            status=FolderStatus(self.magic_config.name, self.status),
        )


    @given(relative_paths(), binary())
    def test_add_single_file(self, relative_path, content):
        """
        Start the service, add a file and check if the operation succeeded.
        """
        to_add = self.magic_path.preauthChild(relative_path)
        to_add.parent().makedirs(ignoreExistingDirectory=True)
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
            to_add.setContent(content)
            files.append(to_add)

        self.snapshot_service.startService()

        list_d = []
        for file in files:
            result_d = self.snapshot_service.add_file(file)
            list_d.append(result_d)

        self.assertThat(
            defer.gatherResults(list_d),
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
        to_add.makedirs()

        self.assertThat(
            self.snapshot_service.add_file(to_add),
            failed(
                matches_failure(
                    APIError,
                    "expected a regular file, .* is a directory"
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
                matches_failure(
                    APIError,
                    "The path being added .*",
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
        self.uncooperator = Cooperator(
            terminationPredicateFactory=lambda: lambda: False,
            scheduler=lambda f: f(),
        )
        self.addCleanup(self.uncooperator.stop)

    def setup_example(self):
        """
        Hypothesis-invoked hook to create per-example state.
        Reset the database before running each test.
        """
        self.temp = FilePath(self.mktemp())
        self.global_db = create_global_configuration(
            self.temp.child("global-db"),
            u"tcp:12345",
            self.temp.child("tahoe-node"),
            u"tcp:localhost:1234",
        )
        self.magic = self.temp.child(u"magic")
        self.magic.makedirs()
        self.db = self.global_db.create_magic_folder(
            u"some-folder",
            self.magic,
            self.author,
            random_immutable(directory=True),
            random_dircap(),
            60,
            None,
        )
        self.snapshot_creator = LocalSnapshotCreator(
            db=self.db,
            author=self.author,
            stash_dir=self.db.stash_path,
            magic_dir=self.db.magic_path,
            tahoe_client=None,
            cooperator=self.uncooperator,
        )

    @given(lists(path_segments(), unique_by=lambda p: p.lower()),
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
            file.setContent(content)

            files.append((file, filename, content))

        for (file, filename, _unused) in files:
            self.assertThat(
                self.snapshot_creator.store_local_snapshot(file),
                succeeded(Always())
            )

        self.assertThat(self.db.get_all_localsnapshot_paths(), HasLength(len(files)))
        for (file, filename, content) in files:
            stored_snapshot = self.db.get_local_snapshot(filename)
            stored_content = stored_snapshot.content_path.getContent()
            path_state = self.db.get_currentsnapshot_pathstate(filename)
            self.assertThat(stored_content, Equals(content))
            self.assertThat(stored_snapshot.parents_local, HasLength(0))
            self.assertThat(
                path_state,
                MatchesStructure(
                    size=Equals(len(content)),
                    mtime_ns=Equals(seconds_to_ns(file.getModificationTime())),
                )
            )

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
        foo.setContent(content1)

        # make sure the store_local_snapshot() succeeds
        self.assertThat(
            self.snapshot_creator.store_local_snapshot(foo),
            succeeded(Always()),
        )

        stored_snapshot1 = self.db.get_local_snapshot(filename)

        # now modify the file with some new content.
        foo.setContent(content2)

        # make sure the second call succeeds as well
        self.assertThat(
            self.snapshot_creator.store_local_snapshot(foo),
            succeeded(Always()),
        )
        stored_snapshot2 = self.db.get_local_snapshot(filename)

        self.assertThat(
            stored_snapshot2.parents_local[0],
            MatchesStructure(
                content_path=Equals(stored_snapshot1.content_path)
            )
        )

    @given(content=binary(min_size=1),
           filename=path_segments(),
    )
    def test_delete_snapshot(self, filename, content):
        """
        Create a snapshot and then a deletion snapshot of it.
        """
        foo = self.magic.child(filename)
        foo.setContent(content)

        # make sure the store_local_snapshot() succeeds
        self.assertThat(
            self.snapshot_creator.store_local_snapshot(foo),
            succeeded(Always()),
        )

        # delete the file
        foo.remove()

        # store a new snapshot
        self.assertThat(
            self.snapshot_creator.store_local_snapshot(foo),
            succeeded(Always()),
        )
        stored_snapshot2 = self.db.get_local_snapshot(filename)

        self.assertThat(
            stored_snapshot2.is_delete(),
            Equals(True),
        )
