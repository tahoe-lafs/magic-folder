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
    cfg = alice.global_config()
    print(cfg)

    # do a thing that'll cause a database WRITE

    async def do_write():
        print("sleeping")
        await twisted_sleep(reactor, .5)
        print("adding")
        await alice.add("some_folder", temp_filepath.path)
        print("done")

    d = ensureDeferred(do_write())

    with cfg.database:
        cursor = cfg.database.cursor()

        while not d.called:
            cursor.execute("SELECT api_endpoint FROM config")
            await twisted_sleep(reactor, 0.0)

    await d
    print("done")
