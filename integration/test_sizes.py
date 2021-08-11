"""
Testing on-grid sizes.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import json

import pytest_twisted
from eliot import start_action
from eliot.twisted import inline_callbacks
from twisted.internet.defer import returnValue

from .test_synchronize import non_lit_content
from .util import twisted_sleep


@inline_callbacks
def get_deep_stats(node, dircap):
    with start_action(
        action_type="integration:tahoe:deep-stats", node=node.name, dircap=dircap
    ) as action:
        tahoe_client = node.tahoe_client()

        resp = yield tahoe_client.http_client.post(
            tahoe_client.url.child("uri").child(dircap).add("t", "stream-manifest")
        )
        manifest = [json.loads(line) for line in (yield resp.content()).splitlines()]
        stats = manifest.pop()["stats"]
        action.add_success_fields(stats=stats)
    returnValue(stats)


@pytest_twisted.inlineCallbacks
def test_sizes(request, reactor, temp_filepath, alice):
    """
    Ensure that our estimate of the on-grid size of a folder is accurate.
    """

    magic = temp_filepath

    # add our magic-folder and re-start
    yield alice.add("local", magic.path)
    local_cfg = alice.global_config().get_magic_folder("local")

    def cleanup():
        pytest_twisted.blockon(alice.leave("local"))

    request.addfinalizer(cleanup)

    # put a file in our folder
    content0 = non_lit_content("zero")
    magic.child("sylvester").setContent(content0)
    yield alice.add_snapshot("local", "sylvester")

    content0 = "zero\n" * 1000
    magic.child("grumpy").setContent(content0)
    yield alice.add_snapshot("local", "grumpy")

    # wait until we've definitely uploaded it
    for _ in range(10):
        yield twisted_sleep(reactor, 1)
        try:
            local_cfg.get_remotesnapshot("sylvester")
            break
        except KeyError:
            pass

    alice_folders = yield alice.list_(True)

    collective_cap = alice_folders["local"]["collective_dircap"]

    stats = yield get_deep_stats(alice, collective_cap)
    # The on grid representation of magic folder consists of mutable and
    # immutable directories and of immutable files. Thus, the sum of these
    # two values is the total on-grid size.
    actual_size = stats["size-immutable-files"] + stats["size-directories"]

    sizes = yield alice.client().tahoe_objects("local")
    estimated_size = sum(sizes)

    assert actual_size < estimated_size < actual_size + 1000
