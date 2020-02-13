# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Common fixtures to let the test suite focus on application logic.
"""

from __future__ import (
    absolute_import,
)

from fixtures import (
    Fixture,
)

from twisted.internet.protocol import (
    ProcessProtocol,
)

class TahoeLAFSNode(Fixture):
    """
    Supply a running Tahoe-LAFS node.
    """
    def __init__(self, reactor, node_directory):
        super(TahoeLAFSNode, self).__init__()
        self.reactor = reactor
        self.node_directory = node_directory

    def _setUp(self):
        self._transport = self.reactor.spawnProcess(
            _TahoeLAFSNodeProtocol(self),
            executable,
            args=[
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



class _TahoeLAFSNodeProtocol(ProcessProtocol):
    pass
