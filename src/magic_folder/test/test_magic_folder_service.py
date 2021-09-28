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
import json
from hyperlink import (
    DecodedURL,
)
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
    find,
)
from hypothesis.strategies import (
    binary,
    integers,
    just,
    sampled_from,
)
from testtools.matchers import (
    Is,
    Always,
    Equals,
    ContainsDict,
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
from ..magicpath import (
    path2magic,
)
from ..config import (
    create_global_configuration,
    create_testing_configuration,
)
from ..status import (
    FolderStatus,
    WebSocketStatusService,
)
from ..snapshot import (
    create_local_author,
)
from ..tahoe_client import (
    create_tahoe_client,
)
from ..downloader import(
    InMemoryMagicFolderFilesystem,
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
    local_authors,
    folder_names,
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
        status_service = WebSocketStatusService(reactor, None)
        folder_status = FolderStatus(name, status_service)
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            folder_status=folder_status,
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            participants=participants,
            scanner_service=Service(),
            clock=reactor,
            magic_file_factory=MagicFileFactory(
                config,
                tahoe_client,
                folder_status,
                local_snapshot_service,
                object(),
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
        magic_path = FilePath(self.mktemp()).asTextMode("utf-8")
        magic_path.asBytesMode("utf-8").makedirs()
        mf_config = global_config.create_magic_folder(
            u"foldername",
            magic_path,
            create_local_author(u"zara"),
            b"URI:DIR2:",
            b"URI:DIR2:",
            60,
            None,
        )

        target_path = magic_path.preauthChild(relative_target_path)
        target_path.asBytesMode("utf-8").parent().makedirs(ignoreExistingDirectory=True)
        target_path.asBytesMode("utf-8").setContent(content)

        clock = task.Clock()
        status_service = WebSocketStatusService(clock, global_config)
        folder_status = FolderStatus(u"foldername", status_service)
        local_snapshot_creator = MemorySnapshotCreator()
        clock = task.Clock()
        local_snapshot_service = LocalSnapshotService(
            mf_config,
            local_snapshot_creator,
            folder_status,
        )

        tahoe_client = object()
        name = u"local-snapshot-service-test"
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=mf_config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            folder_status=folder_status,
            scanner_service=Service(),
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            participants=participants,
            clock=clock,
            magic_file_factory=MagicFileFactory(
                mf_config,
                tahoe_client,
                folder_status,
                local_snapshot_service,
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
