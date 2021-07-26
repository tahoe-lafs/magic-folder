# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Tests for `magic-folder-api`.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

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

    @inline_callbacks
    def test_scan_magic_folder(self):
        """
        Scanning a magic folder creates a snapshot of new files.
        """
        name = "file"
        local = self.magic_path.child(name)
        local.setContent(b"content")

        outcome = yield self.api_cli(
            [
                b"scan-folder",
                b"--folder",
                self.folder_name.encode("utf-8"),
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        snapshot_paths = self.folder_config.get_all_localsnapshot_paths()
        self.assertThat(
            snapshot_paths,
            Equals({name}),
        )

    @inline_callbacks
    def test_scan_magic_folder_missing_name(self):
        """
        If a folder is not passed to ``magic-folder-api scan-folder``,
        an error is returned.
        """
        name = "file"
        local = self.magic_path.child(name)
        local.setContent(b"content")

        outcome = yield self.api_cli(
            [
                b"scan-folder",
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
