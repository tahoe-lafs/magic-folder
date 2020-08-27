from json import (
    loads,
)

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

    print("-"*80)
    print("create zelda")
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
    print("zelda", zelda)

    proto = util._CollectOutputProtocol()
    util._magic_folder_runner(
        proto, reactor, request,
        [
            "--config", zelda.magic_config_directory,
            "list",
        ],
    )
    output = yield proto.done
    print("-" * 80)
    print(output)
    print("-" * 80)
    assert output.strip() == "No magic-folders"

