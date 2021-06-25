# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

from __future__ import (
    absolute_import,
    division,
    print_function,
)

"""
Common fixtures to let the test suite focus on application logic.
"""

from __future__ import (
    absolute_import,
)

from errno import (
    ENOENT,
)

from ..util.encoding import load_yaml, dump_yaml

import attr

from fixtures import (
    Fixture,
)
from hyperlink import (
    DecodedURL,
)

from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from ..tahoe_client import (
    create_tahoe_client,
)
from ..magic_folder import (
    RemoteSnapshotCreator,
)
from ..status import (
    WebSocketStatusService,
)

from ..config import (
    SQLite3DatabaseLocation,
    MagicFolderConfig,
)

@attr.s
class NodeDirectory(Fixture):
    """
    Provide just enough filesystem state to appear to be a Tahoe-LAFS node
    directory.
    """
    path = attr.ib()
    token = attr.ib(default=b"123")

    @property
    def tahoe_cfg(self):
        return self.path.child(u"tahoe.cfg")

    @property
    def node_url(self):
        return self.path.child(u"node.url")

    @property
    def magic_folder_url(self):
        return self.path.child(u"magic-folder.url")

    @property
    def private(self):
        return self.path.child(u"private")

    @property
    def api_auth_token(self):
        return self.private.child(u"api_auth_token")

    @property
    def magic_folder_yaml(self):
        return self.private.child(u"magic_folders.yaml")

    def create_magic_folder(
            self,
            folder_name,
            collective_dircap,
            upload_dircap,
            directory,
            poll_interval,
    ):
        try:
            magic_folder_config_bytes = self.magic_folder_yaml.getContent()
        except IOError as e:
            if e.errno == ENOENT:
                magic_folder_config = {}
            else:
                raise
        else:
            magic_folder_config = load_yaml(magic_folder_config_bytes)

        magic_folder_config.setdefault(
            u"magic-folders",
            {},
        )[folder_name] = {
            u"collective_dircap": collective_dircap,
            u"upload_dircap": upload_dircap,
            u"directory": directory.path,
            u"poll_interval": u"{}".format(poll_interval),
        }
        self.magic_folder_yaml.setContent(dump_yaml(magic_folder_config))

    def _setUp(self):
        self.path.makedirs()
        self.tahoe_cfg.touch()
        self.node_url.setContent(b"http://127.0.0.1:9876/")
        self.magic_folder_url.setContent(b"http://127.0.0.1:5432/")
        self.private.makedirs()
        self.api_auth_token.setContent(self.token)


class RemoteSnapshotCreatorFixture(Fixture):
    """
    A fixture which provides a ``RemoteSnapshotCreator`` connected to a
    ``MagicFolderConfig``.
    """
    def __init__(self, temp, author, upload_dircap, root=None):
        """
        :param FilePath temp: A path where the fixture may write whatever it
            likes.

        :param LocalAuthor author: The author which will be used to sign
            snapshots the ``RemoteSnapshotCreator`` creates.

        :param bytes upload_dircap: The Tahoe-LAFS capability for a writeable
            directory into which new snapshots will be linked.

        :param IResource root: The root resource for the fake Tahoe-LAFS HTTP
            API hierarchy.  The default is one created by
            ``create_fake_tahoe_root``.
        """
        if root is None:
            root = create_fake_tahoe_root()
        self.temp = temp
        self.author = author
        self.upload_dircap = upload_dircap
        self.root = root
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com"),
            self.http_client,
        )

    def _setUp(self):
        self.magic_path = self.temp.child(b"magic")
        self.magic_path.makedirs()

        self.stash_path = self.temp.child(b"stash")
        self.stash_path.makedirs()

        self.poll_interval = 1

        self.config = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash_path,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            self.magic_path,
            self.poll_interval,
            0,
        )

        self.remote_snapshot_creator = RemoteSnapshotCreator(
            config=self.config,
            local_author=self.author,
            tahoe_client=self.tahoe_client,
            upload_dircap=self.upload_dircap,
            status=WebSocketStatusService(),
        )
