from os import mkdir
from os.path import join

import pytest_twisted

from twisted.internet.error import ProcessTerminated
from twisted.internet.endpoints import serverFromString
from twisted.internet.protocol import (
    Protocol,
    Factory,
)

from eliot import start_action

import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")


@pytest_twisted.inlineCallbacks
def test_exit_if_listen_fails(request, reactor, temp_dir, introducer_furl, flog_gatherer):
    """
    'magic-folder run' exits if we can't listen
    """

    with start_action(action_type=u"integration:create:stephen").context():
        node = yield util.MagicFolderEnabledNode.create(
            reactor, request, temp_dir, introducer_furl, flog_gatherer,
            name="stephen",
            tahoe_web_port="tcp:9982:interface=localhost",
            magic_folder_web_port="tcp:19982:interface=localhost",
            storage=True,
        )

    yield node.stop_magic_folder()
    # listen on 19982
    ep = serverFromString(reactor, "tcp:19982:interface=localhost")

    class DummyProtocol(Protocol):
        pass
    port = yield ep.listen(Factory.forProtocol(DummyProtocol))

    # try to start; should exit
    try:
        yield node.start_magic_folder()
    except Exception as e:
        print("got error", e)
        return
    assert False, "should have failed to start"
