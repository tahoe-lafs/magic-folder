from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import json

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    DeferredList,
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
def test_list(request, reactor, tahoe_venv, base_dir, introducer_furl, flog_gatherer):
    """
    the 'tahoe-objects' API works concurrently
    (see also ticket #570)
    """

    yolandi = yield util.MagicFolderEnabledNode.create(
        reactor,
        tahoe_venv,
        request,
        base_dir,
        introducer_furl,
        flog_gatherer,
        name="yolandi",
        tahoe_web_port="tcp:9982:interface=localhost",
        magic_folder_web_port="tcp:19982:interface=localhost",
        storage=True,
    )
    number_of_folders = 20

    output = yield util._magic_folder_runner(
        reactor, request, "yolandi",
        [
            "--config", yolandi.magic_config_directory,
            "list",
        ],
    )
    assert output.strip() == "No magic-folders"

    # make a bunch of folders
    for folder_num in range(number_of_folders):
        magic_dir = FilePath(base_dir).child("yolandi-magic{}".format(folder_num))
        magic_dir.makedirs()

        yield util._magic_folder_runner(
            reactor, request, "yolandi",
            [
                "--config", yolandi.magic_config_directory,
                "add",
                "--author", "laptop",
                "--name", "workstuff{}".format(folder_num),
                magic_dir.path,
            ],
        )

    # concurrently put 1 file into each folder and immediately create
    # a snapshot for it via an API call
    files = []
    for folder_num in range(number_of_folders):
        magic_dir = FilePath(base_dir).child("yolandi-magic{}".format(folder_num))
        with magic_dir.child("a_file_name").open("w") as f:
            f.write("data {:02d}\n".format(folder_num) * 100)
        files.append(
            util._magic_folder_api_runner(
                reactor, request, "yolandi",
                [
                    "--config", yolandi.magic_config_directory,
                    "add-snapshot",
                    "--folder", "workstuff{}".format(folder_num),
                    "--file", "a_file_name",
                ],
            )
        )

    # this is (Snapshot-size, content-size and metadata-size) for the
    # one file we've put in. Although we know the number of bytes in
    # the file we opened, the other sizes may change depending on
    # details of the Snapshot or metadata implementation .. they
    # should however always be identical across all the magic-folders
    # created in this test.
    expected_results = [[416, 800, 189]] * number_of_folders

    # try for 10 seconds to get what we expect. we're waiting for each
    # of the magic-folders to upload their single "a_file_name" items
    # and the slowest should take 6 seconds + upload time ..
    for _ in range(10):
        yield util.twisted_sleep(reactor, 1)
        results = yield DeferredList([
            yolandi.client.tahoe_objects("workstuff{}".format(folder_num))
            for folder_num in range(number_of_folders)
        ])
        errors = [
            fail
            for ok, fail in results
            if not ok
        ]
        assert errors == []

        actual_results = [
            result
            for ok, result in results
            if ok
        ]
        # exit early if we'll pass
        if actual_results == expected_results:
            break

    # check the results
    assert actual_results == expected_results
