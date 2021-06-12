from __future__ import (
    absolute_import,
    division,
    print_function,
)

import json

from twisted.python.filepath import (
    FilePath,
)

import pytest_twisted

from eliot.twisted import (
    inline_callbacks,
)
from eliot import (
    start_action,
)

from . import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")

# we need the eliot decorator too so that start_action works properly;
# the pytest decorator actually only "marks" the function

@inline_callbacks
@pytest_twisted.inlineCallbacks
def test_list(request, reactor, tahoe_venv, temp_dir, introducer_furl, flog_gatherer):
    """
    'magic-folder list' happy-path works
    """

    with start_action(action_type=u"integration:test_list:zelda", include_args=[], include_result=False):
        zelda = yield util.MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            name="zelda",
            tahoe_web_port="tcp:9982:interface=localhost",
            magic_folder_web_port="tcp:19982:interface=localhost",
            storage=True,
        )

    proto = util._CollectOutputProtocol()
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", zelda.magic_config_directory,
            "list",
        ],
    )
    output = yield proto.done
    assert output.strip() == "No magic-folders"

    magic_dir = FilePath(temp_dir).child("zelda-magic")
    magic_dir.makedirs()

    proto = util._CollectOutputProtocol()
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", zelda.magic_config_directory,
            "add",
            "--author", "laptop",
            "--name", "workstuff",
            magic_dir.path,
        ],
    )
    output = yield proto.done

    proto = util._CollectOutputProtocol()
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", zelda.magic_config_directory,
            "list",
            "--json",
        ],
    )
    output = yield proto.done
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
