from os import mkdir
from os.path import join
from json import (
    loads,
)
import base64

import pytest_twisted

from twisted.internet.error import ProcessTerminated
from twisted.python.filepath import (
    FilePath,
)

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


@pytest_twisted.inlineCallbacks
def test_daemon_inititialize(request, reactor, temp_dir):
    """
    'magic-folder init' happy-path works
    """

    node_dir = join(temp_dir, "daemon")
    tahoe_dir = join(temp_dir, "tahoe")
    mkdir(tahoe_dir)
    with open(join(tahoe_dir, "tahoe.cfg"), "w") as f:
        f.write("# a fake config\n")
    with open(join(tahoe_dir, "node.url"), "w") as f:
        f.write('http://localhost:1234/')

    proto = util._CollectOutputProtocol()
    proc = util._magic_folder_runner(
        proto, reactor, request,
        [
            "init",
            "--config", node_dir,
            "--listen-endpoint", "tcp:1234",
            "--node-directory", tahoe_dir,
        ],
    )
    yield proto.done

    proto = util._CollectOutputProtocol()
    proc = util._magic_folder_runner(
        proto, reactor, request,
        [
            "show-config",
            "--config", node_dir,
        ],
    )
    output = yield proto.done
    config = loads(output)

    assert config["api_endpoint"] == "tcp:1234"
    assert config["tahoe_node_directory"] == tahoe_dir
    assert config["magic_folders"] == dict()
    # the API token should at least be base64-decodable and result in 32 bytes of entropy
    assert len(base64.urlsafe_b64decode(config["api_token"].encode("utf8"))) == 32
