# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for the Twisted service which is responsible for a single
magic-folder.
"""

from twisted.python.filepath import (
    FilePath,
)
from twisted.application.service import (
    Service,
    MultiService,
)
from twisted.internet import task
from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
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
from ..magic_file import (
    MagicFileFactory,
)
from ..config import (
    create_testing_configuration,
)
from ..status import (
    FolderStatus,
    EventsWebSocketStatusService,
)
from ..snapshot import (
    create_local_author,
    LocalSnapshot,
)
from ..downloader import (
    InMemoryMagicFolderFilesystem,
)
from ..participants import (
    SnapshotEntry,
    static_participants,
)
from ..util.capabilities import (
    random_immutable,
    random_dircap,
)
from ..util.file import (
    PathState,
)

from .common import (
    SyncTestCase,
    success_result_of,
)
from .strategies import (
    relative_paths,
)
from .test_local_snapshot import (
    MemorySnapshotCreator,
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
        uploader = Service()
        status_service = EventsWebSocketStatusService(reactor, None)
        folder_status = FolderStatus(name, status_service)
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            invite_manager=Service(),
            local_snapshot_service=local_snapshot_service,
            folder_status=folder_status,
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            uploader=uploader,
            participants=participants,
            scanner_service=Service(),
            clock=reactor,
            magic_file_factory=MagicFileFactory(
                config,
                tahoe_client,
                folder_status,
                local_snapshot_service,
                uploader,
                Service(),
                Service(),
                InMemoryMagicFolderFilesystem(),
            ),
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
        global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath(self.mktemp()),
        )
        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        mf_config = global_config.create_magic_folder(
            u"foldername",
            magic_path,
            create_local_author(u"zara"),
            random_immutable(directory=True),
            random_dircap(),
            60,
            None,
        )

        target_path = magic_path.preauthChild(relative_target_path)
        target_path.parent().makedirs(ignoreExistingDirectory=True)
        target_path.setContent(content)

        clock = task.Clock()
        status_service = EventsWebSocketStatusService(clock, global_config)
        folder_status = FolderStatus(u"foldername", status_service)
        local_snapshot_creator = MemorySnapshotCreator()
        clock = task.Clock()
        local_snapshot_service = LocalSnapshotService(
            mf_config,
            local_snapshot_creator,
            folder_status,
        )
        uploader = Service()

        tahoe_client = object()
        name = u"local-snapshot-service-test"
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=mf_config,
            name=name,
            invite_manager=Service(),
            local_snapshot_service=local_snapshot_service,
            folder_status=folder_status,
            scanner_service=Service(),
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            uploader=uploader,
            participants=participants,
            clock=clock,
            magic_file_factory=MagicFileFactory(
                mf_config,
                tahoe_client,
                folder_status,
                local_snapshot_service,
                uploader,
                object(),
                Service(),
                InMemoryMagicFolderFilesystem(),
            ),
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


class LocalStateTests(SyncTestCase):
    """
    Tests for ``MagicFolder.check_local_state``
    """

    def test_update_personal_dmd(self):
        """
        ``check_local_state`` find a local mismatch
        """
        local_snapshot_service = Service()
        tahoe_client = object()
        reactor = task.Clock()
        name = u"local-snapshot-service-test"
        basedir = FilePath(self.mktemp())
        basedir.makedirs()
        tahoedir = FilePath(self.mktemp())
        tahoedir.makedirs()
        magicdir = FilePath(self.mktemp())
        magicdir.makedirs()
        collective_cap = random_dircap()
        upload_cap = random_dircap()

        global_config = create_testing_configuration(basedir, tahoedir)
        author = create_local_author(u"Ida Rhodes")
        config = global_config.create_magic_folder(
            "a folder",
            magicdir,
            author,
            collective_cap,
            upload_cap,
            5,
            5,
        )
        snap = LocalSnapshot(
            "foo",
            author,
            dict(),
            content_path=FilePath("snap content"),
            parents_local=[],
            parents_remote=[],
        )
        config.store_local_snapshot(snap, PathState(size=1234, mtime_ns=555, ctime_ns=555))

        participants = static_participants(
            my_files={
                "foo": SnapshotEntry(
                    random_immutable(),
                    {"version": 1}
                ),
            }
        )
        uploader = Service()
        status_service = EventsWebSocketStatusService(reactor, None)
        folder_status = FolderStatus(name, status_service)
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            invite_manager=Service(),
            local_snapshot_service=local_snapshot_service,
            folder_status=folder_status,
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            uploader=uploader,
            participants=participants,
            scanner_service=Service(),
            clock=reactor,
            magic_file_factory=MagicFileFactory(
                config,
                tahoe_client,
                folder_status,
                local_snapshot_service,
                uploader,
                Service(),
                Service(),
                InMemoryMagicFolderFilesystem(),
            ),
        )
        success_result_of(magic_folder.check_local_state())
