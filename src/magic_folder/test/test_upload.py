import attr

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
)
from twisted.python.filepath import (
    FilePath,
)

from ..magic_folder import (
    UploaderService,
)

from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
)
from eliot import (
    Message,
)

@attr.s
class MemorySnapshotStore(object):
    """
    A way to test Uploader Service with an in-memory database.

    :ivar [FilePath] processed: All of the paths passed to ``process_item``,
        in the order they were passed.
    """
    local_processed = attr.ib(default=attr.Factory(list))
    remote_processed = attr.ib(default=attr.Factory(list))

    def store_local_snapshot(self, path):
        Message.log(
            message_type=u"memory-snapshot-creator:store-local-snapshot",
            path=path.asTextMode("utf-8").path,
        )
        self.local_processed.append(path)

class UploaderServiceTests(SyncTestCase):
    """
    Tests for ``UploaderService``.
    """
    def setUp(self):
        super(UploaderServiceTests, self).setUp()
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()

    def setup_example(self):
        """
        per-example state that is invoked by hypothesis.
        """
        self.snapshot_creator = MemorySnapshotStore()
        self.uploader_service = UploaderService(
            snapshot_creator=self.snapshot_creator,
        )

    @given(path_segments(), binary())
    def test_commit_a_file(self, name, content):
        """
        Add a file into localsnapshot store, start the service which
        should result in a remotesnapshot corresponding to the
        localsnapshot.
        """
        # - start Uploader Service
        # - create a file with random name and random contents
        # - create author
        # - create a local snapshot
        # - push LocalSnapshot object into the SnapshotStore.
        # - this should be picked up by the Uploader Service and should
        #   result in a snapshot cap.
        pass
