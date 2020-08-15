from json import (
    loads,
)

import pytest_twisted

import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")


@pytest_twisted.inlineCallbacks
def test_add(request, reactor, temp_dir, alice):
    """
    'magic-folder add' happy-path works
    """

    proto = util._CollectOutputProtocol()
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", alice.magic_config_directory,
            "add",
            "--name", "test",
            "--author", "laptop",
            alice.magic_directory,
        ],
    )
    yield proto.done

    proto = util._CollectOutputProtocol()
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "show-config",
            "--config", alice.magic_config_directory,
        ],
    )
    output = yield proto.done
    config = loads(output)

    print(output)
    assert "test" in config["magic_folders"]
    mf_config = config["magic_folders"]["test"]
    assert mf_config["name"] == "test"
    assert mf_config["author_name"] == "laptop"
    expected_keys = ["stash_path", "author_private_key"]
    assert all(
        k in mf_config
        for k in expected_keys
    )
