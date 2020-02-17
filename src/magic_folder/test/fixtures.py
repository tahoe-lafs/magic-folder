# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

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

from json import (
    loads,
)

from fixtures import (
    Fixture,
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
        if self._transport is not None:
            self._transport.signalProcess("KILL")
        # Unfortunately Fixtures / testtools doesn't care if we return a
        # Deferred here.

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
