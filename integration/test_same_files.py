"""
Testing synchronizing files between participants
"""

import json

import pytest_twisted
from eliot.twisted import (
    inline_callbacks,
)

from magic_folder.magicpath import (
    magic2path,
)
from magic_folder.util.capabilities import (
    Capability,
)
from .util import (
    twisted_sleep,
)


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_identical_files(request, reactor, temp_filepath, alice, bob):
    """
    Create several copies of a moderately-sized file under different
    file-names and synchronize it
    """

    magic = temp_filepath

    KILO_OF_DATA = b"I am JPEG data!!" * (1024 // 16)
    assert len(KILO_OF_DATA) >= 2**10, "isn't actually a kibibyte"

    def create_random_cat_pic(path, kilobytes):
        with path.open("w") as f:
            for _ in range(kilobytes):
                f.write(KILO_OF_DATA)

    print("creating test data")
    cat_names = [
        'zero.jpeg',
        'one.jpeg',
        'two.jpeg',
        'three.jpeg',
        'four.jpeg',
    ]

    for top_level in cat_names:
        create_random_cat_pic(magic.child(top_level), 256)

    # add this as a new folder
    await alice.add("sames", magic.path)

    def cleanup():
        pytest_twisted.blockon(alice.leave("sames"))
    request.addfinalizer(cleanup)

    # perform a scan, which will create LocalSnapshots for all the
    # files we already created in the magic-folder (but _not_ upload
    # them, necessarily, yet)
    print("start scan")
    await alice.scan("sames")
    print("scan done")

    def find_uploads(data):
        pending = []
        for event in data["events"]:
            if event["kind"].startswith("upload-"):
                pending.append(event)
        return pending

    # wait for a limited time to be complete
    uploads = 0
    for _ in range(10):
        data = json.loads(await alice.status())
        uploads = len(find_uploads(data))
        if uploads == 0:
            break
        await twisted_sleep(reactor, 1)
    assert uploads == 0, "Should be finished uploading"

    errors = [
        evt for evt in data["events"]
        if evt["kind"] == "error"
    ]
    assert errors == [], "Expected zero errors: {}".format(errors)

    recent = await alice.client.recent_changes("sames")
    actual_cats = {cat["relpath"] for cat in recent}
    expected = set(cat_names)
    assert expected == actual_cats, "Data mismatch"

    # confirm that we can navigate Collective -> alice and find the
    # correct Snapshots (i.e. one for every cat-pic)
    folders = await alice.list_(True)

    files = await alice.tahoe_client().list_directory(
        Capability.from_string(folders["sames"]["upload_dircap"])
    )
    names = {
        magic2path(k)
        for k in files.keys()
        if k not in {"@metadata"}
    }
    assert expected == names, "Data mismatch"
