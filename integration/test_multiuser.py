"""
Testing synchronizing files between 3 or more participants
"""

from eliot.twisted import (
    inline_callbacks,
)
import pytest_twisted

from .util import (
    await_file_contents,
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
async def test_found_users(request, reactor, temp_filepath, alice, bob, edmond, fran):
    """
    Invite three other users to a folder, at different times
    """

    magic = temp_filepath.child("magic-alice")
    magic.makedirs()

    await alice.add(request, "seekrits", magic.path)

    # put a file in our folder, in a subdir
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

    # invite / await edmond + fran
    magic_ed = temp_filepath.child("magic-edmond")
    magic_fran = temp_filepath.child("magic-fran")
    await perform_invite(request, "seekrits", alice, "eddy", edmond, magic_ed)
    await perform_invite(request, "seekrits", alice, "fran", fran, magic_fran)

    await await_file_contents(
        magic_ed.child("folder").child("very-secret.txt").path,
        content0,
        timeout=25,
    )
    await await_file_contents(
        magic_fran.child("folder").child("very-secret.txt").path,
        content0,
        timeout=25,
    )

    # make sure nobody has conflicts
    assert find_conflicts(magic) == [], "alice has conflicts"
    assert find_conflicts(magic_bob) == [], "bob has conflicts"
    assert find_conflicts(magic_fran) == [], "fran has conflicts"
    assert find_conflicts(magic_ed) == [], "edmond has conflicts"


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_participant_never_updates(request, reactor, temp_filepath, alice, bob):
    """
    Invite a user to a folder, but they never update.

    This can happen for a variety of reasons, but the participant that
    _is_ updating shouldn't keep re-downloading the (old) Snapshots
    from the never-updating user.
    """

    magic = temp_filepath.child("magic-alice")
    magic.makedirs()

    await alice.add(request, "salmonella", magic.path)

    # put a file in our folder, in a subdir
    content0 = non_lit_content("first content")
    content1 = non_lit_content("second content")
    content2 = non_lit_content("third content")
    magic.child("content.txt").setContent(content0)
    await alice.add_snapshot("salmonella", "content.txt")

    # invite another participant
    magic_bob = temp_filepath.child("magic-bob")
    await perform_invite(request, "salmonella", alice, "robert", bob, magic_bob)

    # wait until bob has sync'd
    await await_file_contents(
        magic_bob.child("content.txt").path,
        content0,
        timeout=25,
    )

    # turn off bob (but arrange to re-start)
    await bob.stop_magic_folder()

    def cleanup():
        pytest_twisted.blockon(bob.start_magic_folder())
    request.addfinalizer(cleanup)

    # do some updates
    magic.child("content.txt").setContent(content1)
    await alice.add_snapshot("salmonella", "content.txt")

    magic.child("content.txt").setContent(content2)
    await alice.add_snapshot("salmonella", "content.txt")

    # now, we monitor "alice" as some scans are done to ensure the
    # client doesn't keep downloading bob's (now out-of-date) update.
    updates = await alice.status_monitor(how_long=15)

    downloads = []  # we should get no downloads
    polls = 0  # ...but at least one "poll"
    for up in updates:
        downs = up.get("downloads", None)
        if downs:
            downloads.extend(downs)
        if up["kind"] == "poll-completed":
            polls += 1
    assert downloads == [], "Alice downloaded {}, but shouldn't".format(downloads)
    assert polls > 0, "Alice should have completed at least one remote poll"

    # ensure nobody has conflicts, just in case
    assert find_conflicts(magic) == [], "alice has conflicts"
    assert find_conflicts(magic_bob) == [], "bob has conflicts"


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_conflicted_users(request, reactor, temp_filepath, alice, bob, edmond):
    """
    Three users all add the same file at the same time, producing conflicts.
    """

    magic = temp_filepath.child("magic-alice")
    magic.makedirs()

    await alice.add(request, "love2share", magic.path)

    # invite some friends
    magic_bob = temp_filepath.child("magic-bob")
    await perform_invite(request, "love2share", alice, "robert", bob, magic_bob)

    # invite / await edmond + fran
    magic_ed = temp_filepath.child("magic-edmond")
    await perform_invite(request, "love2share", alice, "eddy", edmond, magic_ed)

    # add the same file to all three at "the same" time
    content0 = non_lit_content("very-secret")
    magic.child("a-fun-story.txt").setContent(content0)
    magic_bob.child("a-fun-story.txt").setContent(content0)
    magic_ed.child("a-fun-story.txt").setContent(content0)

    await DeferredList([
        alice.add_snapshot("love2share", "a-fun-story.txt"),
        bob.add_snapshot("love2share", "a-fun-story.txt"),
        edmond.add_snapshot("love2share", "a-fun-story.txt"),
    ])
    # we've added all the files on all participants .. they _should_ conflict, but also we
    # shouldn't _keep_ trying to download conflicted updates.

    # now, wait for updates
    all_updates = await DeferredList([
        alice.status_monitor(how_long=20),
        bob.status_monitor(how_long=20),
        edmond.status_monitor(how_long=20),
    ])
    # ensure we don't "keep downloading" when there's a conflict
    for st, updates in all_updates:
        assert st, "status streaming failed"
        assert len([e for e in updates if e["kind"] == "download-queued"]) < 2, "too many downloads queued"

    # everyone should have a conflict though...
    assert find_conflicts(magic) != [], "alice should have conflicts"
    assert find_conflicts(magic_bob) != [], "bob should have conflicts"
    assert find_conflicts(magic_ed) != [], "edmond should have conflicts"


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_read_only_participant(request, reactor, temp_filepath, alice, bob, edmond):
    """
    Invite two other users, one read-only.
    """

    magic = temp_filepath.child("magic-alice")
    magic.makedirs()
    await alice.add(request, "readonly", magic.path)

    # put a file in our folder, in a subdir
    content0 = non_lit_content("first content")
    magic.child("content.txt").setContent(content0)
    await alice.add_snapshot("readonly", "content.txt")

    # invite another participant
    magic_bob = temp_filepath.child("magic-bob")
    await perform_invite(request, "readonly", alice, "robert", bob, magic_bob)

    # wait until bob has sync'd
    await await_file_contents(
        magic_bob.child("content.txt").path,
        content0,
        timeout=25,
    )

    # invite a read-only participant
    magic_ed = temp_filepath.child("edmond")
    await perform_invite(request, "readonly", alice, "eddy", edmond, magic_ed, read_only=True)


    # ensure nobody has conflicts, just in case
    assert find_conflicts(magic) == [], "alice has conflicts"
    assert find_conflicts(magic_bob) == [], "bob has conflicts"
