"""
Testing synchronizing files between 3 or more participants
"""

from eliot.twisted import (
    inline_callbacks,
)
import pytest_twisted

from .util import (
    find_conflicts,
)
from twisted.internet.defer import DeferredList


def non_lit_content(s):
    # type: (str) -> bytes
    """
    Pad the given string so it is long enough to not fit in a tahoe literal
    URI.
    """
    # The max size of data that will be stored in a literal tahoe cap is 55.
    # See allmydata.immutable.upload.Uploader.URI_LIT_SIZE_THRESHOLD
    # We don't need to be exactly longer than that threshold, as long as we
    # are over it.
    return "{} {}\n".format(s, "." * max(55 - len(s), 0)).encode("utf8")


async def perform_invite(request, folder_name, inviter, invitee_name, invitee, invitee_magic_fp, read_only=False):
    invitee_magic_fp.makedirs()

    code, magic_proto, process_transport = await inviter.invite(folder_name, invitee_name)
    await invitee.join(
        code,
        folder_name,
        invitee_magic_fp.path,
        invitee_name,
        poll_interval=1,
        scan_interval=1,
        read_only=read_only,
    )

    def cleanup_invitee():
        pytest_twisted.blockon(invitee.leave(folder_name))
    request.addfinalizer(cleanup_invitee)

    await magic_proto.exited
    print(f"{invitee_name} successfully invited to {folder_name}")


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_resolve_two_users(request, reactor, temp_filepath, alice, bob):
    """
    Two users both add the same file at the same time, producing conflicts.

    One user resolves the conflict.
    """

    magic = temp_filepath.child("magic-alice")
    magic.makedirs()

    await alice.add(request, "conflict", magic.path)

    # invite some friends
    magic_bob = temp_filepath.child("magic-bob")
    await perform_invite(request, "conflict", alice, "robert", bob, magic_bob)

    # add the same file at "the same" time
    content0 = non_lit_content("very-secret")
    magic.child("summertime.txt").setContent(content0)
    magic_bob.child("summertime.txt").setContent(content0)

    await DeferredList([
        alice.add_snapshot("conflict", "summertime.txt"),
        bob.add_snapshot("conflict", "summertime.txt"),
    ])
    # we've added all the files on both participants

    # wait for updates
    await DeferredList([
        alice.status_monitor(how_long=20),
        bob.status_monitor(how_long=20),
    ])

    # everyone should have a conflict...
    assert find_conflicts(magic) != [], "alice should have conflicts"
    assert find_conflicts(magic_bob) != [], "bob should have conflicts"

    # resolve the conflict
    await alice.resolve("conflict", magic.child("summertime.txt").path, "theirs")

    # wait for updates
    await DeferredList([
        alice.status_monitor(how_long=20),
        bob.status_monitor(how_long=20),
    ])

    # no more conflicts
    assert find_conflicts(magic) == [], "alice has conflicts"
    assert find_conflicts(magic_bob) == [], "bob has conflicts"
