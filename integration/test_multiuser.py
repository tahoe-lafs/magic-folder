"""
Testing synchronizing files between participants
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

from magic_folder.util.capabilities import (
    Capability,
)
from .util import (
    await_file_contents,
    await_file_vanishes,
    ensure_file_not_created,
    twisted_sleep,
    database_retry,
    find_conflicts,
)


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


def add_snapshot(node, folder_name, path):
    """
    Take a snapshot of the given path in the given magic folder.

    :param MagicFolderEnabledNode node: The node on which to take the snapshot.
    """
    return node.add_snapshot(folder_name, path)


def scan_folder(node, folder_name, path):
    """
    Scan the given magic folder. This should cause the given path to be
    snapshotted.

    :param MagicFolderEnabledNode node: The node on which to do the scan.
    """
    return node.scan_folder(folder_name)


async def perform_invite(request, folder_name, inviter, invitee_name, invitee, invitee_magic_fp):
    invitee_magic_fp.makedirs()

    code, magic_proto, process_transport = await inviter.invite(folder_name, invitee_name)
    await invitee.join(
        code,
        folder_name,
        invitee_magic_fp.path,
        invitee_name,
        poll_interval=1,
        scan_interval=1,
    )

    def cleanup_invitee():
        pytest_twisted.blockon(invitee.leave(folder_name))
    request.addfinalizer(cleanup_invitee)

    await magic_proto.exited
    print(f"{invitee_name} successfully invited to {folder_name}")


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_three_users(request, reactor, temp_filepath, alice, bob, edmond):
    """
    Invite two other users to a folder, at different times
    """

    magic = temp_filepath.child("magic-alice")
    magic.makedirs()

    await alice.add("seekrits", magic.path)
    local_cfg = alice.global_config().get_magic_folder("seekrits")

    def cleanup():
        pytest_twisted.blockon(alice.leave("seekrits"))
    request.addfinalizer(cleanup)

    # put a file in our folder
    content0 = non_lit_content("very-secret")
    folder = magic.child("folder")
    folder.makedirs()
    folder.child("very-secret.txt").setContent(content0)
    await alice.add_snapshot("seekrits", "folder/very-secret.txt")

    # now, invite some friends
    magic_bob = temp_filepath.child("magic-bob")
    await perform_invite(request, "seekrits", alice, "robert", bob, magic_bob)

    # wait until bob has sync'd before adding our next friend
    await await_file_contents(
        magic_bob.child("folder").child("very-secret.txt").path,
        content0,
        timeout=25,
    )

    # invite / await edmond
    magic_ed = temp_filepath.child("magic-edmond")
    await perform_invite(request, "seekrits", alice, "eddy", edmond, magic_ed)

    await await_file_contents(
        magic_ed.child("folder").child("very-secret.txt").path,
        content0,
        timeout=25,
    )

    assert find_conflicts(magic) == [], "alice has conflicts"
    assert find_conflicts(magic_bob) == [], "bob has conflicts"
    assert find_conflicts(magic_ed) == [], "edmond has conflicts"
