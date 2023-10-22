import json
import pytest_twisted
from eliot.twisted import (
    inline_callbacks,
)

from magic_folder.util.capabilities import (
    Capability,
)

from .util import (
    twisted_sleep,
)


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_multiple_outstanding_downloads(request, reactor, alice, temp_filepath):
    """
    The status API shows many outstanding downloads during a simulated
    recovery flow.
    """

    filenames = ["one_____", "two_____", "three___"]
    magic0 = temp_filepath.child("outstanding0")
    magic0.makedirs()

    # create a folder with several files in it
    await alice.add(request, "outstanding0", magic0.path, author="laptop")
    for fname in filenames:
        p = magic0.child(fname)
        with p.open("w") as f:
            f.write(fname.encode("utf8") * 1024*1024*5)
            await alice.add_snapshot("outstanding0", p.path)

    alice_folders = await alice.list_(True)
    zero_cap = Capability.from_string(alice_folders["outstanding0"]["upload_dircap"]).to_readonly().danger_real_capability_string()

    # create a folder with no files in it
    magic1 = temp_filepath.child("outstanding1")
    magic1.makedirs()
    await alice.add(request, "outstanding1", magic1.path, author="desktop")

    # add the "other" folder as a participant .. simulate recovery
    await alice.add_participant("outstanding1", "old", zero_cap)

    # monitor the "downloads" status for up to 10 seconds, and collect
    # any relpath's mentiond there -- we should see (at some point)
    # everything in "filenames"
    start = reactor.seconds()
    noticed = set()
    while (reactor.seconds() - start < 10) and len(noticed) != len(filenames):
        status_data = await alice.status()
        status = json.loads(status_data)
        downloads = [
            down
            for down in status["events"]
            if down["kind"].startswith("download-")
        ]
        noticed = noticed.union({
            d["relpath"]
            for d in downloads
        })
        await twisted_sleep(reactor, .2)

    print("noticed downloads: {}".format(noticed))
    assert noticed == set(filenames)
