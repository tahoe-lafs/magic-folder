from __future__ import (
    absolute_import,
    division,
    print_function,
)

from os import mkdir
from os.path import join
from json import (
    loads,
)
import base64

import pytest_twisted

from . import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")


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
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", node_dir,
            "init",
            "--listen-endpoint", "tcp:1234",
            "--node-directory", tahoe_dir,
        ],
    )
    yield proto.done

    proto = util._CollectOutputProtocol()
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", node_dir,
            "show-config",
        ],
    )
    output = yield proto.done
    config = loads(output)

    assert config["api_endpoint"] == "tcp:1234"
    assert config["tahoe_node_directory"] == tahoe_dir
    assert config["magic_folders"] == dict()
    # the API token should at least be base64-decodable and result in 32 bytes of entropy
    assert len(base64.urlsafe_b64decode(config["api_token"].encode("utf8"))) == 32
