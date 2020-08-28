# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for the Twisted service which is responsible for a single
magic-folder.
"""

from __future__ import (
    absolute_import,
    print_function,
    division,
)
from hyperlink import (
    DecodedURL,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.application.service import (
    Service,
)
from twisted.internet import task
from hypothesis import (
    given,
    find,
)
from hypothesis.strategies import (
    binary,
    just,
    one_of,
    integers,
)
from testtools.matchers import (
    Is,
    Always,
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)
from ..magic_folder import (
    MagicFolder,
    LocalSnapshotService,
)
from ..config import (
    create_global_configuration,
)
from ..tahoe_client import (
    TahoeClient,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)

from .common import (
    SyncTestCase,
)
from .strategies import (
    relative_paths,
    path_segments,
    local_authors,
    tahoe_lafs_dir_capabilities,
    tahoe_lafs_readonly_dir_capabilities,
    folder_names,
)
from .test_local_snapshot import (
    MemorySnapshotCreator as LocalMemorySnapshotCreator,
)

class MagicFolderServiceTests(SyncTestCase):
    """
    Tests for ``MagicFolder``.
    """
    def test_local_snapshot_service_child(self):
        """
        ``MagicFolder`` adds the service given as ``LocalSnapshotService`` to
        itself as a child.
        """
        local_snapshot_service = Service()
        tahoe_client = object()
        reactor = object()
        name = u"local-snapshot-service-test"
        config = object()
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=Service(),
            initial_participants=participants,
            clock=reactor,
        )
        self.assertThat(
            local_snapshot_service.parent,
            Is(magic_folder),
        )

    @given(
        relative_target_path=relative_paths(),
        content=binary(),
    )
    def test_create_local_snapshot(self, relative_target_path, content):
        """
        ``MagicFolder.local_snapshot_service`` can be used to create a new local
        snapshot for a file in the folder.
        """
        magic_path = FilePath(self.mktemp())
        magic_path.asBytesMode("utf-8").makedirs()

        target_path = magic_path.preauthChild(relative_target_path).asBytesMode("utf-8")
        target_path.asBytesMode("utf-8").parent().makedirs(ignoreExistingDirectory=True)
        target_path.setContent(content)

        local_snapshot_creator = LocalMemorySnapshotCreator()
        local_snapshot_service = LocalSnapshotService(magic_path, local_snapshot_creator)
        clock = object()

        tahoe_client = object()
        name = u"local-snapshot-service-test"
        config = object()
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=Service(),
            initial_participants=participants,
            clock=clock,
        )
        magic_folder.startService()
        self.addCleanup(magic_folder.stopService)

        adding = magic_folder.local_snapshot_service.add_file(
            target_path,
        )
        self.assertThat(
            adding,
            succeeded(Always()),
        )

        self.assertThat(
            local_snapshot_creator.processed,
            Equals([target_path]),
        )

    def test_start_uploader_service(self):
        """
        MagicFolder.uploader_service creates a new remote snapshot
        when a file is added into the folder.
        """
        magic_path = FilePath(self.mktemp())
        magic_path.asBytesMode("utf-8").makedirs()

        local_snapshot_creator = LocalMemorySnapshotCreator()
        local_snapshot_service = LocalSnapshotService(magic_path, local_snapshot_creator)
        clock = task.Clock()

        # create RemoteSnapshotCreator and UploaderService
        uploader_service = Service()

        tahoe_client = object()
        name = u"local-snapshot-service-test"
        config = object()
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=uploader_service,
            initial_participants=participants,
            clock=clock,
        )
        magic_folder.startService()
        self.addCleanup(magic_folder.stopService)

        self.assertThat(
            uploader_service.running,
            Equals(True),
        )


LOCAL_AUTHOR = find(local_authors(), lambda x: True)

class MagicFolderFromConfigTests(SyncTestCase):
    """
    Tests for ``MagicFolder.from_config``.
    """
    @given(
        folder_names(),
        relative_paths(),
        path_segments(),
        relative_paths(),
        just(LOCAL_AUTHOR),
        one_of(
            tahoe_lafs_dir_capabilities(),
            tahoe_lafs_readonly_dir_capabilities(),
        ),
        tahoe_lafs_dir_capabilities(),
        integers(min_value=1, max_value=10000),
        binary(),
    )
    def test_uploader_service(self, name, file_path, relative_magic_path, relative_state_path, author, collective_dircap, upload_dircap, poll_interval, content):
        """
        ``MagicFolder.from_config`` creates an ``UploaderService``
        which will sometimes upload snapshots using the given Tahoe
        client object.
        """
        reactor = task.Clock()

        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = TahoeClient(
            DecodedURL.from_text(U"http://example.invalid./"),
            http_client,
        )

        basedir = FilePath(self.mktemp())
        global_config = create_global_configuration(
            basedir,
            u"tcp:-1",
            FilePath(u"/non-tahoe-directory"),
            u"tcp:-1",
        )

        magic_path = basedir.preauthChild(relative_magic_path)
        magic_path.asBytesMode("utf-8").makedirs()

        statedir = basedir.child(u"state")
        state_path = statedir.asTextMode("utf-8").preauthChild(relative_state_path)

        target_path = magic_path.asTextMode("utf-8").preauthChild(file_path)
        target_path.asBytesMode("utf-8").parent().makedirs(ignoreExistingDirectory=True)
        target_path.asBytesMode("utf-8").setContent(content)

        global_config.create_magic_folder(
            name,
            magic_path,
            state_path,
            author,
            collective_dircap,
            upload_dircap,
            poll_interval,
        )

        magic_folder = MagicFolder.from_config(
            reactor,
            tahoe_client,
            name,
            global_config,
        )

        magic_folder.startService()
        self.addCleanup(magic_folder.stopService)

        self.assertThat(
            magic_folder.uploader_service.running,
            Equals(True),
        )

        # check if uploader service actually got the parameters that
        # was passed
        self.assertThat(
            magic_folder.uploader_service._poll_interval,
            Equals(poll_interval),
        )

        self.assertThat(
            magic_folder.uploader_service._remote_snapshot_creator._local_author,
            Equals(author),
        )

        self.assertThat(
            magic_folder.folder_name,
            Equals(name),
        )

        # add a file. This won't actually add a file until we advance
        # the clock.
        d = magic_folder.local_snapshot_service.add_file(
            target_path,
        )

        self.assertThat(
            d,
            succeeded(Always()),
        )
