from __future__ import (
    absolute_import,
    division,
    print_function,
)

import pytest_twisted


@pytest_twisted.inlineCallbacks
def test_add(request, reactor, temp_dir, alice):
    """
    'magic-folder add' happy-path works
    """

    yield alice.add(
        "test",
        alice.magic_directory,
        author="laptop",
    )

    config = yield alice.show_config()

    assert "test" in config["magic_folders"]
    mf_config = config["magic_folders"]["test"]
    assert mf_config["name"] == "test"
    assert mf_config["author_name"] == "laptop"
    expected_keys = ["stash_path", "author_private_key"]
    assert all(
        k in mf_config
        for k in expected_keys
    )
