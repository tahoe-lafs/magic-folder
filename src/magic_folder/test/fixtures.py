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

from sys import (
    executable,
)
from os import (
    environ,
)

from errno import (
    ENOENT,
)

from ..util.encoding import load_yaml, dump_yaml

from json import (
    loads,
)

import attr

from fixtures import (
    Fixture,
)
from hyperlink import (
    DecodedURL,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.defer import (
    Deferred,
    succeed,
    inlineCallbacks,
)
from twisted.internet.protocol import (
    ProcessProtocol,
)
from twisted.web.client import (
    Agent,
    readBody,
)

from eliot import (
    Message,
)

from magic_folder.util.eliotutil import (
    log_call_deferred,
)

from .tahoe_lafs import (
    create,
    create_introducer,
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

class INTRODUCER(object):
    ready_bytes = b"introducer running"

class CLIENT(object):
    ready_bytes = b"client running"

class RunningTahoeLAFSNode(Fixture):
    """
    Supply a running Tahoe-LAFS node.
    """
    def __init__(self, reactor, node_directory, node_kind=CLIENT):
        super(RunningTahoeLAFSNode, self).__init__()
        self.reactor = reactor
        self.node_directory = node_directory
        self._for_ended = []
        self._for_ready = []
        self._ready = False
        self.node_kind = node_kind

    def _setUp(self):
        self._transport = self.reactor.spawnProcess(
            _TahoeLAFSNodeProtocol(self, self.node_kind),
            executable,
            args=[
                executable,
                b"-m",
                b"allmydata.scripts.runner",
                b"--node-directory", self.node_directory.path,
                b"run",
            ],
            env=environ,
        )

    def _cleanUp(self):
        # Unfortunately Fixtures / testtools doesn't care if we return a
        # Deferred here.
        if self._transport is not None:
            Message.log(
                message_type=u"test:cli:running-tahoe-lafs-node:signal",
                node_kind=self.node_kind.__name__,
            )
            self._transport.signalProcess("KILL")
        else:
            Message.log(
                message_type=u"test:cli:running-tahoe-lafs-node:no-signal",
                node_kind=self.node_kind.__name__,
            )

    def use_on(self, testcase):
        testcase.useFixture(self)
        # Since Fixture._cleanUp can't return a Deferred that's respected,
        # hook into clean up here.
        testcase.addCleanup(self.stop)
        # Now wait for the node to be ready for use.
        return self.wait_for_ready()

    @inlineCallbacks
    def connected_enough(self):
        url = self.node_directory.child(u"node.url").getContent().strip()
        url += b"?t=json"
        agent = Agent(self.reactor)
        while True:
            response = yield agent.request(b"GET", url)
            if response.code == 200:
                body = yield readBody(response)
                status = loads(body)
                if any(
                        server[u"last_received_data"] is not None
                        for server
                        in status[u"servers"]
                ):
                    break
            yield deferLater(self.reactor, 0.05, lambda: None)

    @log_call_deferred(u"test:cli:running-tahoe-lafs-node:stop")
    def stop(self):
        self._cleanUp()
        return self.wait_for_exit()

    def wait_for_exit(self):
        if self._transport is None:
            return succeed(None)
        self._for_ended.append(Deferred())
        return self._for_ended[-1]

    def wait_for_ready(self):
        if self._ready:
            return succeed(None)
        self._for_ready.append(Deferred())
        return self._for_ready[-1]

    def _process_is_ready(self):
        self._ready = True
        for_ready = self._for_ready
        self._for_ready = []
        for d in for_ready:
            d.callback(None)

    def _processEnded(self, reason):
        self._transport = None
        waiting = self._for_ended
        self._for_ended = []
        for d in waiting:
            d.callback(None)
        waiting = self._for_ready
        self._for_ready = []
        for d in waiting:
            d.errback(reason)


class _TahoeLAFSNodeProtocol(ProcessProtocol):
    def __init__(self, fixture, node_kind):
        self._fixture = fixture
        self._node_kind = node_kind
        self._out = b""

    def processEnded(self, reason):
        self._fixture._processEnded(reason)

    def outReceived(self, data):
        self._out += data
        if self._node_kind.ready_bytes in self._out:
            self._fixture._process_is_ready()
            self._out = b""



class SelfConnectedClient(Fixture):
    """
    Supply a running Tahoe-LAFS node which provides both a client gateway and
    storage services and which is connected to itself via a running Tahoe-LAFS
    introducer.
    """
    def __init__(self, reactor):
        super(SelfConnectedClient, self).__init__()
        self.reactor = reactor

    @inlineCallbacks
    def use_on(self, testcase):
        """
        Use this fixture on the given testcase.

        This is like ``testcase.useFixture(self)`` except that it supports the
        asynchronous cleanup that is required by this fixture.
        """
        testcase.useFixture(self)

        self.tempdir = FilePath(testcase.mktemp())
        self.tempdir.makedirs()

        # Create an introducer.  This is necessary to have our node introduce
        # its own storage to itself.  This avoids needing to run a second node
        # for storage which would likely require an introducer anyway.
        introducer_directory = self.tempdir.child(u"introducer")
        self.introducer = yield create_introducer(self, introducer_directory)
        introducer = RunningTahoeLAFSNode(
            self.reactor,
            introducer_directory,
            INTRODUCER,
        )
        yield introducer.use_on(testcase)

        # Read out its Foolscap server location - only after it is started.
        introducer_furl = introducer_directory.child(
            u"private"
        ).child(
            u"introducer.furl"
        ).getContent()

        # Create a node which will be the client and also act as storage.
        self.node_directory = self.tempdir.child(u"client-and-storage")
        yield create(self.node_directory, configuration={
            u"node": {
                u"web.port": u"tcp:0:interface=127.0.0.1",
            },
            u"storage": {
                u"enabled": True,
            },
            u"client": {
                u"shares.needed": 1,
                u"shares.happy": 1,
                u"shares.total": 1,
                u"introducer.furl": introducer_furl,
            },
        })
        self.client = RunningTahoeLAFSNode(self.reactor, self.node_directory)
        yield self.client.use_on(testcase)
        yield self.client.connected_enough()


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
        )

        self.remote_snapshot_creator = RemoteSnapshotCreator(
            config=self.config,
            local_author=self.author,
            tahoe_client=self.tahoe_client,
            upload_dircap=self.upload_dircap,
            status=WebSocketStatusService(),
        )
