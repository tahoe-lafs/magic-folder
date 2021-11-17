from __future__ import absolute_import, division, print_function, unicode_literals

import json
import pytest_twisted
from twisted.internet.error import ProcessTerminated

from magic_folder.util.capabilities import (
    to_readonly_capability,
)

from .util import (
    await_file_contents,
    ensure_file_not_created,
    twisted_sleep,
)


@pytest_twisted.inlineCallbacks
def test_multiple_outstanding_downloads(request, reactor, alice, temp_filepath):
    """
    The status API shows many outstanding downloads during a simulated
    recovery flow.
    """

    filenames = ["one_____", "two_____", "three___"]
    magic0 = temp_filepath.child("outstanding0")
    magic0.makedirs()

    # create a folder with several files in it
    yield alice.add("outstanding0", magic0.path, author="laptop")
    for fname in filenames:
        p = magic0.child(fname)
        with p.open("w") as f:
            f.write(fname * 1024*1024*5)
            yield alice.add_snapshot("outstanding0", p.path)

    alice_folders = yield alice.list_(True)
    zero_cap = to_readonly_capability(alice_folders["outstanding0"]["upload_dircap"])

    # create a folder with no files in it
    magic1 = temp_filepath.child("outstanding1")
    magic1.makedirs()
    yield alice.add("outstanding1", magic1.path, author="desktop")

    def cleanup():
        pytest_twisted.blockon(alice.leave("outstanding0"))
        pytest_twisted.blockon(alice.leave("outstanding1"))
    request.addfinalizer(cleanup)

    # add the "other" folder as a participant .. simulate recovery
    yield alice.add_participant("outstanding1", "old", zero_cap)

    downloads = None
    while downloads is None:
        status_data = yield alice.status()
        status = json.loads(status_data)
        one = status["state"]["folders"].get("outstanding1", None)
        if one:
            print(json.dumps(one, indent=4))
            if one["downloads"]:
                downloads = one["downloads"]
        yield twisted_sleep(reactor, .2)

    print("found downloads: {}".format(downloads))
    assert {d["relpath"] for d in downloads} == set(filenames)
