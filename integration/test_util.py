import pytest
import pytest_twisted
from eliot.twisted import (
    inline_callbacks,
)
from twisted.internet.defer import ensureDeferred
from .util import twisted_sleep



@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_sqlite3(request, reactor, alice, temp_filepath):
    print(alice)
    print(dir(alice))
    print(alice.magic_folder)
    print(dir(alice.magic_folder))
    cfg = alice.global_config()
    print(cfg)

    # do a thing that'll cause a database WRITE

    async def do_write():
        print("sleeping")
        await twisted_sleep(reactor, .1)
        print("adding")
        await alice.add("some_folder", temp_filepath.path)
        print("done")

    d = ensureDeferred(do_write())
    print(d)

    while not d.called:
        print(cfg.api_endpoint)
        await twisted_sleep(reactor, 0.0001)

    await d
    print("done")
