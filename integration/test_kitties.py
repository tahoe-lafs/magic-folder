from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Testing synchronizing files between participants
"""

import json
import random

import pytest_twisted

from .util import (
    twisted_sleep,
)



@pytest_twisted.inlineCallbacks
def test_kittens(request, reactor, temp_filepath, alice, bob):
    """
    Create a series of large files -- including in sub-directories --
    for an initial, new magic-folder. (This simulates the 'Cat Pics'
    test data collection used by GridSync).
    """

    magic = temp_filepath

    KILO_OF_DATA = "I am JPEG data!!" * (1024 // 16)
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
    yield alice.add("kitties", magic.path)
    kitties_cfg = alice.global_config().get_magic_folder("kitties")

    def cleanup():
        pytest_twisted.blockon(alice.leave("kitties"))
    request.addfinalizer(cleanup)

    print("scanning...")
    yield alice.scan("kitties")
    print("done")
    state = yield alice.dump_state("kitties")
    print(state)

    status = yield alice.status()
    data = json.loads(status)
    print(json.dumps(data, indent=4))

    assert data["state"]["synchronizing"] == False, "Should be finished"

    kitties = data["state"]["folders"]["kitties"]
    assert kitties["errors"] == [], "Expected zero errors"
    actual_cats = {cat["relpath"] for cat in kitties["recent"]}
    expected = set(cat_names + ["subdir/{}".format(n) for n in cat_names])
    assert expected == actual_cats, "Data mismatch"

    state = yield alice.dump_state("kitties")
    print(state)
    # XXX also confirm that we can navigate Collective -> alice
    # and find the correct Snapshots (i.e. one for every cat-pic)
