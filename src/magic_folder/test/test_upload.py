from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import io

from json import (
    dumps,
    loads,
)

import attr

from re import (
    escape,
)

from zope.interface import (
    implementer,
)

from testtools.matchers import (
    MatchesPredicate,
    Always,
    Equals,
)
from testtools import (
    ExpectedException,
)
from testtools.twistedsupport import (
    succeeded,
)
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
from twisted.web.resource import (
    ErrorPage,
)
from ..uploader import (
    IRemoteSnapshotCreator,
    UploaderService,
)
from ..snapshot import (
    create_local_author,
    create_snapshot,
)
from ..magicpath import (
    path2magic,
)
from ..util.capabilities import is_immutable_directory_cap, to_verify_capability
from ..util.file import PathState
from twisted.internet import task

from .common import (
    SyncTestCase,
)
from .strategies import (
    path_segments,
    relative_paths,
    tahoe_lafs_dir_capabilities,
)

from .fixtures import (
    RemoteSnapshotCreatorFixture,
)

from magic_folder.tahoe_client import (
    TahoeAPIError,
)


class RemoteSnapshotCreatorTests(SyncTestCase):
    """
    Tests for ``RemoteSnapshotCreator``.
    """
    def setUp(self):
        super(RemoteSnapshotCreatorTests, self).setUp()
        self.author = create_local_author(u"alice")

    @given(
        mangled_name=relative_paths().map(path2magic),
        content=binary(),
        upload_dircap=tahoe_lafs_dir_capabilities(),
    )
    def test_commit_a_file(self, mangled_name, content, upload_dircap):
        """
        Add a file into localsnapshot store, start the service which
        should result in a remotesnapshot corresponding to the
        localsnapshot.
        """
        f = self.useFixture(RemoteSnapshotCreatorFixture(
            temp=FilePath(self.mktemp()),
            author=self.author,
            upload_dircap=upload_dircap,
        ))
        config = f.config
        remote_snapshot_creator = f.remote_snapshot_creator

        # Make the upload dircap refer to a dirnode so the snapshot creator
        # can link files into it.
        f.root._uri.data[upload_dircap] = dumps([
            u"dirnode",
            {u"children": {}},
        ])

        # create a local snapshot
        data = io.BytesIO(content)

        d = create_snapshot(
            name=mangled_name,
            author=self.author,
            data_producer=data,
            snapshot_stash_dir=config.stash_path,
            parents=[],
        )

        snapshots = []
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        # push LocalSnapshot object into the SnapshotStore.
        # This should be picked up by the Uploader Service and should
        # result in a snapshot cap.
        config.store_local_snapshot(snapshots[0])
        config.store_currentsnapshot_state(mangled_name, PathState(0, 0, 0))

        d = remote_snapshot_creator.upload_local_snapshots()
        self.assertThat(
            d,
            succeeded(Always()),
        )

        remote_snapshot_cap = config.get_remotesnapshot(mangled_name)

        # Verify that the new snapshot was linked in to our upload directory.
        self.assertThat(
            loads(f.root._uri.data[upload_dircap])[1][u"children"],
            Equals({
                mangled_name: [
                    u"dirnode", {
                        u"ro_uri": remote_snapshot_cap.decode("utf-8"),
                        u"verify_uri": to_verify_capability(remote_snapshot_cap),
                        u"mutable": False,
                        u"format": u"CHK",
                    },
                ],
            }),
        )


        # test whether we got a capability
        self.assertThat(
            remote_snapshot_cap,
            MatchesPredicate(
                is_immutable_directory_cap,
                "%r is not a immuutable directory Tahoe-LAFS URI",
            ),
        )

        with ExpectedException(KeyError, escape(repr(mangled_name))):
            config.get_local_snapshot(mangled_name)

    @given(
        path_segments(),
        lists(
            binary(),
            min_size=1,
            max_size=2,
        ),
        tahoe_lafs_dir_capabilities(),
    )
    def test_write_snapshot_to_tahoe_fails(self, name, contents, upload_dircap):
        """
        If any part of a snapshot upload fails then the metadata for that snapshot
        is retained in the local database and the snapshot content is retained
        in the stash.
        """
        broken_root = ErrorPage(500, "It's broken.", "It's broken.")

        f = self.useFixture(RemoteSnapshotCreatorFixture(
            temp=FilePath(self.mktemp()),
            author=self.author,
            root=broken_root,
            upload_dircap=upload_dircap,
        ))
        config = f.config
        remote_snapshot_creator = f.remote_snapshot_creator

        snapshots = []
        parents = []
        for content in contents:
            data = io.BytesIO(content)
            d = create_snapshot(
                name=name,
                author=self.author,
                data_producer=data,
                snapshot_stash_dir=config.stash_path,
                parents=parents,
            )
            d.addCallback(snapshots.append)
            self.assertThat(
                d,
                succeeded(Always()),
            )
            config.store_local_snapshot(snapshots[-1])
            parents = [snapshots[-1]]

        local_snapshot = snapshots[-1]

        d = remote_snapshot_creator.upload_local_snapshots()
        self.assertThat(
            d,
            succeeded(Always()),
        )

        self.eliot_logger.flushTracebacks(TahoeAPIError)

        self.assertEqual(
            local_snapshot,
            config.get_local_snapshot(name),
        )
        self.assertThat(
            local_snapshot.content_path.getContent(),
            Equals(content),
        )


@implementer(IRemoteSnapshotCreator)
@attr.s
class MemorySnapshotCreator(object):
    _uploaded = attr.ib(default=0)

    def upload_local_snapshots(self):
        self._uploaded += 1


class UploaderServiceTests(SyncTestCase):
    """
    Tests for ``UploaderService``.
    """
    def setUp(self):
        super(UploaderServiceTests, self).setUp()
        self.poll_interval = 1
        self.clock = task.Clock()
        self.remote_snapshot_creator = MemorySnapshotCreator()
        self.uploader_service = UploaderService(
            poll_interval=self.poll_interval,
            clock=self.clock,
            remote_snapshot_creator=self.remote_snapshot_creator,
        )

    def test_commit_a_file(self):
        # start Uploader Service
        self.uploader_service.startService()
        self.addCleanup(self.uploader_service.stopService)

        # We want processing to start immediately on startup in case there was
        # work left over from the last time we ran.  So there should already
        # have been one upload attempt by now.
        self.assertThat(
            self.remote_snapshot_creator._uploaded,
            Equals(1),
        )

        # advance the clock manually, which should result in the
        # polling of the db for uncommitted LocalSnapshots in the db
        # and then check for remote snapshots
        self.clock.advance(self.poll_interval)

        self.assertThat(
            self.remote_snapshot_creator._uploaded,
            Equals(2),
        )
