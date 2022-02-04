"""
Performance analysis of magic-folder (and tahoe-lafs)
"""

import sys
from functools import partial

from eliot import (
    log_message,
)
from eliot.twisted import (
    inline_callbacks,
)
import pytest
import pytest_twisted
from treq.client import (
    HTTPClient,
)
from twisted.web.client import (
    Agent,
)
from hyperlink import (
    DecodedURL,
)

from magic_folder.util.capabilities import (
    Capability,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)
from .util import (
    await_file_contents,
    await_file_vanishes,
    ensure_file_not_created,
    twisted_sleep,
    database_retry,
    _create_node,
)


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_tahoe_performance(request, reactor, tahoe_venv, flog_gatherer, introducer_furl, temp_filepath):
    """
    Measure performance of uploading a single large file to a single
    Tahoe-LAFS node (that is providing its own storage).
    """

    # XXX will need to run a "peformance introducer" separately so we
    # don't get the other storage-servers involved .. I guess.

    basedir = temp_filepath.child("tahoe")
    basedir.makedirs()

    tahoe_storage = await _create_node(
        reactor,
        tahoe_venv,
        request,
        basedir.path,
        introducer_furl,
        flog_gatherer,
        name="perf_storage0",
        web_port=u"tcp:8889:interface=localhost",
        storage=True,
        needed=1,
        happy=1,
        total=1,
    )
    # XXX cleanup
    print(dir(tahoe_storage))

    tahoe = await _create_node(
        reactor,
        tahoe_venv,
        request,
        basedir.path,
        introducer_furl,
        flog_gatherer,
        name="perf_client",
        web_port=u"tcp:8888:interface=localhost",
        storage=False,
        needed=1,
        happy=1,
        total=1,
    )
    # XXX cleanup

    client = create_tahoe_client(
        DecodedURL.from_text("http://localhost:8888/"),
        HTTPClient(Agent(reactor)),
    )

    w = {"servers": []}
    while len(w["servers"]) < 1:
        print("waiting for storage-server connections")
        w = await client.get_welcome()
        await twisted_sleep(reactor, 1)

    total_bytes = 1000000 * 8  # 8 MB
    total_bytes = 1000000 * 80  # 80 MB
    total_bytes = 1000000 * 800  # 80 MB
    data_producer = b"eight..." * int(total_bytes / 8.0)

    trials = []
    total_bps = 0.0

    for _ in range(10):
        start = reactor.seconds()
        c = await client.create_immutable(data_producer)
        elapsed = reactor.seconds() - start
        bps = float(total_bytes) / elapsed
        trials.append({
            "elapsed": elapsed,
            "bytes_per_second": bps,
        })
        total_bps += bps
        print(
            "create_immutable: {}b in {}s: {} bytes/second".format(
                total_bytes,
                elapsed,
                bps,
            )
        )

    average_bps = total_bps / len(trials)
    print("average bps: {}".format(average_bps))
    print("MB/s: {}".format(average_bps / 1000000.0))
