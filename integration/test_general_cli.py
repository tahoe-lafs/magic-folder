from os.path import join
from json import (
    loads,
)
import base64

import pytest_twisted
from eliot.twisted import (
    inline_callbacks,
)

from . import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_daemon_inititialize(request, reactor, temp_filepath):
    """
    'magic-folder init' happy-path works
    """

    node_dir = temp_filepath.child("daemon")
    tahoe_dir = temp_filepath.child("tahoe")
    tahoe_dir.makedirs()
    tahoe_dir.child("tahoe.cfg").setContent(b"# a fake config\n")
    tahoe_dir.child("node.url").setContent(b"http://localhost:1234/")

    await util._magic_folder_runner(
        reactor, request, "daemon",
        [
            "--config", node_dir.path,
            "init",
            "--listen-endpoint", "tcp:1234",
            "--node-directory", tahoe_dir.path,
        ],
    )

    output = await util._magic_folder_runner(
        reactor, request, "daemon",
        [
            "--config", node_dir.path,
            "show-config",
        ],
    )
    config = loads(output)

    assert config["api_endpoint"] == "tcp:1234"
    assert config["tahoe_node_directory"] == tahoe_dir.path
    assert config["magic_folders"] == dict()
    # the API token should at least be base64-decodable and result in 32 bytes of entropy
    assert len(base64.urlsafe_b64decode(config["api_token"].encode("utf8"))) == 32


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_daemon_migrate(request, reactor, alice, temp_filepath):
    """
    'magic-folder migrate' happy-path works
    """

    node_dir = temp_filepath.child("test-daemon-migrate")

    # if we're depending on a "new" tahoe (which we should) then
    # there's no "tahoe magic-folder" to create "legacy" config for us
    # to migrate. So, we create an (empty) config.
    with open(join(alice.node_directory, "private", "magic_folders.yaml"), "w") as f:
        f.write("magic-folders: {}\n")

    await util._magic_folder_runner(
        reactor, request, "migrate",
        [
            "--config", node_dir.path,
            "migrate",
            "--listen-endpoint", "tcp:1234",
            "--node-directory", alice.node_directory,
            "--author", "test",
        ],
    )

    output = await util._magic_folder_runner(
        reactor, request, "migrate",
        [
            "--config", node_dir.path,
            "show-config",
        ],
    )
    config = loads(output)

    assert config["api_endpoint"] == "tcp:1234"
    assert config["magic_folders"] == dict()
    # the API token should at least be base64-decodable and result in 32 bytes of entropy
    assert len(base64.urlsafe_b64decode(config["api_token"].encode("utf8"))) == 32
