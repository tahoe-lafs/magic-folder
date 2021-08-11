# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for the Twisted service which is responsible for a single
magic-folder.
"""

from __future__ import absolute_import, division, print_function

import json

from hyperlink import DecodedURL
from hypothesis import find, given
from hypothesis.strategies import binary, integers, just, sampled_from
from testtools.matchers import Always, ContainsDict, Equals, Is
from testtools.twistedsupport import succeeded
from twisted.application.service import MultiService, Service
from twisted.internet import task
from twisted.python.filepath import FilePath

from ..config import create_global_configuration, create_testing_configuration
from ..magic_folder import LocalSnapshotService, MagicFolder
from ..magicpath import path2magic
from ..snapshot import create_local_author
from ..status import FolderStatus, WebSocketStatusService
from ..tahoe_client import create_tahoe_client
from ..testing.web import create_fake_tahoe_root, create_tahoe_treq_client
from .common import SyncTestCase
from .strategies import folder_names, local_authors, relative_paths
from .test_local_snapshot import MemorySnapshotCreator, MemoryUploaderService


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
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=Service(),
            folder_status=FolderStatus(name, status_service),
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            participants=participants,
            scanner_service=Service(),
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
            MemoryUploaderService(),
        )

        tahoe_client = object()
        name = u"local-snapshot-service-test"
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=mf_config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=Service(),
            folder_status=folder_status,
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            participants=participants,
            scanner_service=Service(),
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
        When the ``MagicFolder`` service is started the given uploader service is
        also started.
        """
        global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath(self.mktemp()),
        )
        magic_path = FilePath(self.mktemp())
        magic_path.asBytesMode("utf-8").makedirs()
        config = global_config.create_magic_folder(
            u"foldername",
            magic_path,
            create_local_author(u"zara"),
            b"URI:DIR2:",
            b"URI:DIR2:",
            60,
            None,
        )

        clock = task.Clock()
        status_service = WebSocketStatusService(clock, None)
        folder_status = FolderStatus(u"foldername", status_service)
        local_snapshot_creator = MemorySnapshotCreator()
        local_snapshot_service = LocalSnapshotService(
            config,
            local_snapshot_creator,
            folder_status,
            uploader_service=Service(),
        )

        # create RemoteSnapshotCreator and UploaderService
        uploader_service = Service()

        tahoe_client = object()
        name = u"local-snapshot-service-test"
        participants = object()
        magic_folder = MagicFolder(
            client=tahoe_client,
            config=config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=uploader_service,
            folder_status=folder_status,
            remote_snapshot_cache=Service(),
            downloader=MultiService(),
            participants=participants,
            scanner_service=Service(),
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
        just(LOCAL_AUTHOR),
        sampled_from([b"URI:DIR2:", b"URI:DIR2-RO:"]),
        integers(min_value=1, max_value=10000),
        binary(),
    )
    def test_uploader_service(
        self,
        name,
        file_path,
        author,
        collective_cap_kind,
        poll_interval,
        content,
    ):
        """
        ``MagicFolder.from_config`` creates an ``UploaderService`` which will
        upload snapshots using the given Tahoe client object.
        """
        reactor = task.Clock()
        scan_interval = None

        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )

        ignored, upload_dircap = root.add_mutable_data(
            b"URI:DIR2:",
            json.dumps(
                [
                    u"dirnode",
                    {u"children": {}},
                ]
            ),
        )

        ignored, collective_dircap = root.add_mutable_data(
            collective_cap_kind,
            json.dumps(
                [
                    u"dirnode",
                    {u"children": {}},
                ]
            ),
        )

        basedir = FilePath(self.mktemp()).asTextMode("utf-8")
        global_config = create_global_configuration(
            basedir.child("config"),
            u"tcp:-1",
            FilePath(u"/non-tahoe-directory"),
            u"tcp:127.0.0.1:-1",
        )

        magic_path = FilePath(self.mktemp()).asTextMode("utf-8")
        magic_path.asBytesMode("utf-8").makedirs()

        target_path = magic_path.preauthChild(file_path)
        target_path.asBytesMode("utf-8").parent().makedirs(ignoreExistingDirectory=True)
        target_path.asBytesMode("utf-8").setContent(content)

        global_config.create_magic_folder(
            name,
            magic_path,
            author,
            collective_dircap,
            upload_dircap,
            poll_interval,
            scan_interval,
        )

        magic_folder = MagicFolder.from_config(
            reactor,
            tahoe_client,
            name,
            global_config,
            WebSocketStatusService(reactor, global_config),
        )

        magic_folder.startService()
        self.addCleanup(magic_folder.stopService)

        self.assertThat(
            magic_folder.uploader_service.running,
            Equals(True),
        )

        self.assertThat(
            magic_folder.uploader_service._remote_snapshot_creator._local_author,
            Equals(author),
        )

        self.assertThat(
            magic_folder.folder_name,
            Equals(name),
        )

        # add a file.
        d = magic_folder.local_snapshot_service.add_file(
            target_path,
        )

        self.assertThat(
            d,
            succeeded(Always()),
        )

        def children():
            return json.loads(root._uri.data[upload_dircap])[1][u"children"]

        self.assertThat(
            children(),
            ContainsDict({path2magic(file_path): Always()}),
            "Children dictionary {!r} did not contain expected path".format(
                children,
            ),
        )
