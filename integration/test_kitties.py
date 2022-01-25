"""
Testing synchronizing files between participants
"""

import json
import random

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
async def test_kittens(request, reactor, temp_filepath, alice):
    """
    Create a series of large files -- including in sub-directories --
    for an initial, new magic-folder. (This simulates the 'Cat Pics'
    test data collection used by GridSync).
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
        'Garfield.jpeg',
        'Cheshire.jpeg',
        'Grumpy.jpeg',
        'lolcat.jpeg',
        'Waffles.jpeg',
    ]

    for top_level in cat_names:
        size = random.randrange(200, 356)
        create_random_cat_pic(magic.child(top_level), size)
        print("  {} {}KiB".format(top_level, size))

    magic.child("subdir").makedirs()
    for sub_level in cat_names:
        size = random.randrange(60, 200)
        create_random_cat_pic(magic.child("subdir").child(sub_level), size)
        print("  subdir/{} {}KiB".format(sub_level, size))

    # add this as a new folder
    await alice.add("kitties", magic.path)

    def cleanup():
        pytest_twisted.blockon(alice.leave("kitties"))
    request.addfinalizer(cleanup)

    # perform a scan, which will create LocalSnapshots for all the
    # files we already created in the magic-folder (but _not_ upload
    # them, necessarily, yet)
    print("start scan")
    await alice.scan("kitties")
    print("scan done")

    # wait for a limited time to be complete
    for _ in range(10):
        st = await alice.status()
        data = json.loads(st.strip())
        if data["state"]["synchronizing"] is False:
            break
        await twisted_sleep(reactor, 1)
    assert data["state"]["synchronizing"] is False, "Should be finished uploading"

    kitties = data["state"]["folders"]["kitties"]
    assert kitties["errors"] == [], "Expected zero errors"
    actual_cats = {cat["relpath"] for cat in kitties["recent"]}
    expected = set(cat_names + ["subdir/{}".format(n) for n in cat_names])
    assert expected == actual_cats, "Data mismatch"

    # confirm that we can navigate Collective -> alice and find the
    # correct Snapshots (i.e. one for every cat-pic)
    folders = await alice.list_(True)

    files = await alice.tahoe_client().list_directory(
        Capability.from_string(folders["kitties"]["upload_dircap"])
    )
    names = {
        magic2path(k)
        for k in files.keys()
        if k not in {"@metadata"}
    }
    assert expected == names, "Data mismatch"
