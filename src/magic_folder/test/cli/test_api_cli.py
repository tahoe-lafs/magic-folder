# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Tests for `magic-folder-api`.
"""

from eliot.twisted import inline_callbacks
from testtools.matchers import Equals
from twisted.internet import reactor
from twisted.python.filepath import FilePath

from ..common import AsyncTestCase
from ..fixtures import MagicFolderNode
from .common import api_cli, cli


class ScanMagicFolder(AsyncTestCase):
    def api_cli(self, argv):
        return api_cli(argv, self.node.global_config, self.node.http_client)

    def cli(self, argv):
        return cli(argv, self.node.global_config, self.node.http_client)

    @inline_callbacks
    def setUp(self):
        """
        Create a Tahoe-LAFS node which can contain some magic folder configuration
        and run it.
        """
        yield super(ScanMagicFolder, self).setUp()

        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self.folder_name = "default"
        folders = {
            self.folder_name: {
                "magic-path": self.magic_path,
                "author-name": "author",
                "poll-interval": 60,
                "scan-interval": None,
                "admin": True,
            }
        }

        self.config_dir = FilePath(self.mktemp())
        self.node = MagicFolderNode.create(
            reactor,
            self.config_dir,
            folders=folders,
            start_folder_services=False,
        )

        self.folder_config = self.node.global_config.get_magic_folder(self.folder_name)
        self.folder_service = self.node.global_service.get_folder_service(
            self.folder_name
        )

        self.folder_service.local_snapshot_service.startService()
        self.addCleanup(self.folder_service.local_snapshot_service.stopService)

        self.folder_service.uploader_service.startService()
        self.addCleanup(self.folder_service.uploader_service.stopService)

        def clean():
            folder = self.node.global_service.get_folder_service(self.folder_name)
            return folder.scanner_service._file_factory.finish()
        self.addCleanup(clean)

    @inline_callbacks
    def test_scan_magic_folder(self):
        """
        Scanning a magic folder creates a snapshot of new files.
        """
        relpath = "file"
        local = self.magic_path.child(relpath)
        local.setContent(b"content")

        outcome = yield self.api_cli(
            [
                u"scan",
                u"--folder",
                self.folder_name,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        snapshot_paths = self.folder_config.get_all_snapshot_paths()
        self.assertThat(
            snapshot_paths,
            Equals({relpath}),
        )

    @inline_callbacks
    def test_scan_magic_folder_missing_name(self):
        """
        If a folder is not passed to ``magic-folder-api scan``,
        an error is returned.
        """
        relpath = "file"
        local = self.magic_path.child(relpath)
        local.setContent(b"content")

        outcome = yield self.api_cli(
            [
                u"scan",
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "--folder / -n is required",
            outcome.stderr,
        )

    @inline_callbacks
    def test_poll_magic_folder(self):
        """
        Polling a magic folder causes the remote to be downloaded
        """

        outcome = yield self.api_cli(
            [
                u"poll",
                u"--folder",
                self.folder_name,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @inline_callbacks
    def test_poll_magic_folder_missing_name(self):
        """
        If a folder is not passed to ``magic-folder-api poll``,
        an error is returned.
        """
        relpath = "file"
        local = self.magic_path.child(relpath)
        local.setContent(b"content")

        outcome = yield self.api_cli(
            [
                u"poll",
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "--folder / -n is required",
            outcome.stderr,
        )
