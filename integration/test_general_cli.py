from os import mkdir
from os.path import join
from json import (
    loads,
)
import base64

import pytest_twisted

import util

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


@pytest_twisted.inlineCallbacks
def test_daemon_migrate(request, reactor, alice, temp_dir):
    """
    'magic-folder migrate' happy-path works
    """

    node_dir = join(temp_dir, "test-daemon-migrate")

    # if we're depending on a "new" tahoe (which we should) then
    # there's no "tahoe magic-folder" to create "legacy" config for us
    # to migrate. So, we create an (empty) config.
    with open(join(alice.node_directory, "private", "magic_folders.yaml"), "w") as f:
        f.write("magic-folders: {}\n")

    proto = util._DumpOutputProtocol(None)
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", node_dir,
            "migrate",
            "--listen-endpoint", "tcp:1234",
            "--node-directory", alice.node_directory,
            "--author", "test",
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
    assert config["magic_folders"] == dict()
    # the API token should at least be base64-decodable and result in 32 bytes of entropy
    assert len(base64.urlsafe_b64decode(config["api_token"].encode("utf8"))) == 32
