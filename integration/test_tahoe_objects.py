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
from testtools.assertions import assert_that
from testtools.matchers import (
    Equals,
    HasLength,
    AllMatch,
    MatchesAll,
    AfterPreprocessing,
)

from . import util

# see "conftest.py" for the fixtures (e.g. "magic_folder")


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_list_tahoe_objects(request, reactor, tahoe_venv, base_dir, introducer_furl, flog_gatherer):
    """
    the 'tahoe-objects' API works concurrently
    (see also ticket #570)
    """

    yolandi = await util.MagicFolderEnabledNode.create(
        reactor,
        tahoe_venv,
        request,
        base_dir,
        introducer_furl,
        flog_gatherer,
        name="yolandi",
        tahoe_web_port="tcp:9983:interface=localhost",
        magic_folder_web_port="tcp:19983:interface=localhost",
        storage=True,
    )
    number_of_folders = 20
    folder_names = ["workstuff{}".format(n) for n in range(number_of_folders)]

    # make a bunch of folders
    for folder_name in folder_names:
        magic_dir = FilePath(base_dir).child(folder_name)
        magic_dir.makedirs()

        await yolandi.client.add_folder(
            folder_name,
            author_name="yolandi",
            local_path=magic_dir,
            poll_interval=10,
            scan_interval=10,
        )

    # concurrently put 1 file into each folder and immediately create
    # a snapshot for it via an API call
    files = []
    for folder_num, folder_name in enumerate(folder_names):
        magic_dir = FilePath(base_dir).child(folder_name)
        with magic_dir.child("a_file_name").open("w") as f:
            f.write("data {:02d}\n".format(folder_num).encode("utf8") * 100)
        files.append(
            yolandi.client.add_snapshot(
                folder_name,
                "a_file_name",
            )
        )

    # Each folder should produce [416, 800, 190] for the sizes -- this
    # is (Snapshot-size, content-size and metadata-size) for the one
    # file we've put in.  .. except the first one depends on
    # Snapshot's implementation and the last one depends on metadata
    # details, so we only want to assert that they're all the same.
    # expected_results = [[416, 800, 190]] * number_of_folders

    # The "if res else None" clauses below are because we use this in
    # the loop (to potentially succeed early), and some of the results
    # may be empty for a few iterations / seconds
    matches_expected_results = MatchesAll(
        # this says that all the content capabilities (2nd item)
        # should be size 800
        AfterPreprocessing(
            lambda results: [res[1] if res else None for res in results],
            AllMatch(Equals(800))
        ),
        # this says that there should be exactly one thing in the set
        # of all the pairs of the Snapshot (1st item) and metadata
        # (3rd item) sizes .. that is, that all the Snapshot sizes are
        # the same and all the metadata sizes are the same.
        AfterPreprocessing(
            lambda results: {(res[0], res[2]) if res else None for res in results},
            HasLength(1)
        )
    )

    # try for 15 seconds to get what we expect. we're waiting for each
    # of the magic-folders to upload their single "a_file_name" items
    # so that they each have one Snapshot in Tahoe-LAFS
    for _ in range(15):
        await util.twisted_sleep(reactor, 1)
        results = await DeferredList([
            yolandi.client.tahoe_objects(folder_name)
            for folder_name in folder_names
        ])
        # if any of the queries fail, we fail the test
        errors = [
            fail
            for ok, fail in results
            if not ok
        ]
        assert errors == [], "At least one /tahoe-objects query failed"

        actual_results = [
            result
            for ok, result in results
            if ok
        ]
        # exit early if we'll pass the test
        if matches_expected_results.match(actual_results) is None:
            break

    # check the results
    assert_that(actual_results, matches_expected_results)
