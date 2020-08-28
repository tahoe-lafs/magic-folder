
import pytest_twisted

from eliot import (
    start_action,
)

import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")


@pytest_twisted.inlineCallbacks
def test_list(request, reactor, temp_dir, introducer_furl, flog_gatherer):
    """
    'magic-folder list' happy-path works
    """

    with start_action(action_type=u"integration:test_list:zelda", include_args=[], include_result=False):
        zelda = yield util.MagicFolderEnabledNode.create(
            reactor,
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

