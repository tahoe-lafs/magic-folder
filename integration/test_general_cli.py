from os import mkdir
from os.path import join

import pytest_twisted

from twisted.internet.error import ProcessTerminated

import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")


@pytest_twisted.inlineCallbacks
def test_web_port_required(request, reactor, temp_dir, introducer_furl):
    """
    'magic-folder run' requires --web-port option
    """

    # enough of a node-dir for "magic-folder" to run .. maybe we
    # should create a real one?
    node_dir = join(temp_dir, "webport")
    mkdir(node_dir)
    with open(join(node_dir, "tahoe.cfg"), "w") as f:
        f.write('')
    with open(join(node_dir, "node.url"), "w") as f:
        f.write('http://localhost/')

    # run "magic-folder run" without --web-port which should error
    proto = util._CollectOutputProtocol()
    proc = util._magic_folder_runner(
        proto, reactor, request,
        [
            "--node-directory", node_dir,
            "run",
        ],
    )
    try:
        yield proto.done
    except ProcessTerminated as e:
        assert e.exitCode != 0
        assert u"Must specify a listening endpoint with --web-port" in proto.output.getvalue()
        return

    assert False, "Expected an error from magic-folder"
    print("output: '{}'".format(proto.output.getvalue()))

