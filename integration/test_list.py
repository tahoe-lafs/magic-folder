from __future__ import absolute_import, division, print_function

import json

import pytest_twisted
from eliot import start_action
from eliot.twisted import inline_callbacks
from twisted.python.filepath import FilePath

from . import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")

# we need the eliot decorator too so that start_action works properly;
# the pytest decorator actually only "marks" the function


@inline_callbacks
@pytest_twisted.inlineCallbacks
def test_list(request, reactor, tahoe_venv, base_dir, introducer_furl, flog_gatherer):
    """
    'magic-folder list' happy-path works
    """

    with start_action(
        action_type=u"integration:test_list:zelda",
        include_args=[],
        include_result=False,
    ):
        zelda = yield util.MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            base_dir,
            introducer_furl,
            flog_gatherer,
            name="zelda",
            tahoe_web_port="tcp:9982:interface=localhost",
            magic_folder_web_port="tcp:19982:interface=localhost",
            storage=True,
        )

    output = yield util._magic_folder_runner(
        reactor,
        request,
        "zelda",
        [
            "--config",
            zelda.magic_config_directory,
            "list",
        ],
    )
    assert output.strip() == "No magic-folders"

    magic_dir = FilePath(base_dir).child("zelda-magic")
    magic_dir.makedirs()

    output = yield util._magic_folder_runner(
        reactor,
        request,
        "zelda",
        [
            "--config",
            zelda.magic_config_directory,
            "add",
            "--author",
            "laptop",
            "--name",
            "workstuff",
            magic_dir.path,
        ],
    )

    output = yield util._magic_folder_runner(
        reactor,
        request,
        "zelda",
        [
            "--config",
            zelda.magic_config_directory,
            "list",
            "--json",
        ],
    )
    data = json.loads(output)

    assert list(data.keys()) == ["workstuff"]
    assert data["workstuff"]["name"] == "workstuff"
    assert int(data["workstuff"]["poll_interval"]) == 60
    assert data["workstuff"]["magic_path"] == magic_dir.path
    assert data["workstuff"]["is_admin"] is True

    # make sure we didn't reveal secrets
    assert "signing_key" not in data["workstuff"]["author"]
    assert "upload_dircap" not in data["workstuff"]
    assert "collective_dircap" not in data["workstuff"]
