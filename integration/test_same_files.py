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

    # wait for a limited time to be complete
    for _ in range(10):
        st = await alice.status()
        data = json.loads(st.strip())
        if data["state"]["synchronizing"] is False:
            break
        await twisted_sleep(reactor, 1)
    assert data["state"]["synchronizing"] is False, "Should be finished uploading"

    sames = data["state"]["folders"]["sames"]
    assert sames["errors"] == [], "Expected zero errors"
    actual_cats = {cat["relpath"] for cat in sames["recent"]}
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
