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
