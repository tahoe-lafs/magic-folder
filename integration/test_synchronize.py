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


def periodic_scan(node, folder_name, path):
    """
    Wait for the given magic folder to run a periodic scan.  This
    should cause the given path to be snapshotted.

    :param MagicFolderEnabledNode node: The node on which to do the scan.
    """
    from twisted.internet import reactor
    log_message(message_type="integration:wait_for_scan", node=node.name, folder=folder_name)
    # XXX need a better way than "wait 3 seconds" to know if the scan is completed...
    return twisted_sleep(reactor, 3.0)


@pytest.fixture(name='periodic_scan')
def enable_periodic_scans(magic_folder_nodes, monkeypatch):
    """
    A fixture causes magic folders to have periodic scans enabled (with
    interval of 1s), and returns a function to take a snapshot of a file (that
    waits for the scanner to run).
    """
    for node in magic_folder_nodes.values():
        monkeypatch.setattr(node, "add", partial(node.add, scan_interval=1))
    return periodic_scan


@pytest.fixture(
    params=[
        add_snapshot,
        scan_folder,
        pytest.lazy_fixture('periodic_scan'),
    ]
)
def take_snapshot(request, magic_folder_nodes):
    """
    Pytest fixture that parametrizes different ways of having
    magic-folder take a local snapshot of a given file.

    - use the `POST /v1/magic-folder/<folder-name>/snapshot` endpoint
      to request the snapshot directly.
    - use the `PUT /v1/magic-folder/<folder-name>/scan` endpoint to request
      a scan, which will cause a snapshot to be taken.

    :returns Callable[[MagicFolderEnabledNode, unicode, unicode], Deferred[None]]:
        A callable that takes a node, folder name, and relative path to a file
        that should be snapshotted.
    """
    return request.param


@inline_callbacks
@pytest_twisted.ensureDeferred
@pytest.mark.skipif(sys.platform.startswith("win"), reason="suspend() doesn't work on windows")
async def test_local_snapshots(request, reactor, temp_filepath, alice, bob, take_snapshot):
    """
    Create several snapshots while our Tahoe client is offline.
    """

    magic = temp_filepath

    # add our magic-folder and re-start
    await alice.add("local", magic.path)
    local_cfg = alice.global_config().get_magic_folder("local")

    def cleanup():
        pytest_twisted.blockon(alice.leave("local"))
    request.addfinalizer(cleanup)

    # put a file in our folder
    content0 = non_lit_content("zero")
    magic.child("sylvester").setContent(content0)
    await take_snapshot(alice, "local", "sylvester")

    # wait until we've definitely uploaded it
    former_remote = await database_retry(reactor, 10, local_cfg.get_remotesnapshot, "sylvester")
    assert former_remote is not None, "Didn't find remote; upload failed?"

    x = await alice.dump_state("local")
    print(x)

    # turn off Tahoe
    alice.pause_tahoe()

    try:
        # add several snapshots
        content1 = non_lit_content("one")
        magic.child("sylvester").setContent(content1)
        await take_snapshot(alice, "local", "sylvester")
        content2 = non_lit_content("two")
        magic.child("sylvester").setContent(content2)
        await take_snapshot(alice, "local", "sylvester")
        content3 = non_lit_content("three")
        magic.child("sylvester").setContent(content3)
        await take_snapshot(alice, "local", "sylvester")

        x = await alice.dump_state("local")
        print(x)

        # XXX this fails (esp. on mac-os) sometimes because we've
        # already got a database lock (presumably in the production
        # code)
        # https://github.com/LeastAuthority/magic-folder/issues/569
        all_paths = await database_retry(reactor, 10, local_cfg.get_all_localsnapshot_paths)
        assert all_paths == {"sylvester"}
        snap = await database_retry(reactor, 10, local_cfg.get_local_snapshot, "sylvester")
        # we should have 3 snapshots total, each one the parent of the next
        assert len(snap.parents_local) == 1
        assert len(snap.parents_local[0].parents_local) == 1
        assert len(snap.parents_local[0].parents_local[0].parents_local) == 0
        assert len(snap.parents_local[0].parents_local[0].parents_remote) == 1

    finally:
        # turn Tahoe back on
        alice.resume_tahoe()

    # local snapshots should turn into remotes...and thus change our
    # remote snapshot pointer
    found = False
    for _ in range(10):
        if len(local_cfg.get_all_localsnapshot_paths()) == 0:
            if local_cfg.get_remotesnapshot("sylvester") != former_remote:
                found = True
                break
        await twisted_sleep(reactor, 1)
    assert found, "Expected 'sylvester' to be (only) a remote-snapshot"


@inline_callbacks
@pytest_twisted.ensureDeferred
@pytest.mark.parametrize("relpath", ["dinah", "nested/felicia"])
async def test_create_then_recover(request, reactor, temp_filepath, alice, bob, take_snapshot, relpath):
    """
    Test a version of the expected 'recover' workflow:
    - make a magic-folder on device 'alice'
    - add a file
    - create a Snapshot for the file
    - change the file
    - create another Snapshot for the file

    - recovery workflow:
    - create a new magic-folder on device 'bob'
    - add the 'alice' Personal DMD as a participant
    - the latest version of the file should appear

    - bonus: the old device is found!
    - update the file in the original
    - create a Snapshot for the file (now has 3 versions)
    - the update should appear on the recovery device
    """

    # "alice" contains the 'original' magic-folder
    # "bob" contains the 'recovery' magic-folder
    original_folder = temp_filepath.child("cats")
    recover_folder = temp_filepath.child("kitties")

    original_file = original_folder.preauthChild(relpath)
    original_file.parent().makedirs()
    recover_file = recover_folder.preauthChild(relpath)
    recover_file.parent().makedirs()

    # add our magic-folder and re-start
    await alice.add("original", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("original"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = non_lit_content("zero")
    original_file.setContent(content0)
    await take_snapshot(alice, "original", relpath)

    # update the file (so now there's two versions)
    content1 = non_lit_content("one")
    original_file.setContent(content1)
    await take_snapshot(alice, "original", relpath)

    # create the 'recovery' magic-folder
    await bob.add("recovery", recover_folder.path)

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("recovery"))
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = Capability.from_string(alice_folders["original"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await await_file_contents(
        recover_file.path,
        content1,
        timeout=25,
    )

    # in the (ideally rare) case that the old device is found *and* a
    # new snapshot is uploaded, we put an update into the 'original'
    # folder. This also tests the normal 'update' flow as well.
    content2 = non_lit_content("two")
    original_file.setContent(content2)
    await take_snapshot(alice, "original", relpath)

    # the new content should appear in the 'recovery' folder
    await await_file_contents(
        recover_file.path,
        content2,
    )


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_internal_inconsistency(request, reactor, temp_filepath, alice, bob, take_snapshot):
    # FIXME needs docstring
    original_folder = temp_filepath.child("cats")
    recover_folder = temp_filepath.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    await alice.add("internal", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("internal"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = non_lit_content("zero")
    original_folder.child("fluffy").setContent(content0)
    await take_snapshot(alice, "internal", "fluffy")

    # create the 'rec' magic-folder
    await bob.add("rec", recover_folder.path)

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("rec"))
    request.addfinalizer(cleanup_recovery)

    # add the 'internal' magic-folder as a participant in the
    # 'rec' folder
    alice_ro_cap = Capability.from_string(alice_folders["internal"]["upload_dircap"]).to_readonly()
    await bob.add_participant("rec", "alice", alice_ro_cap.danger_real_capability_string())

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await await_file_contents(
        recover_folder.child("fluffy").path,
        content0,
        timeout=25,
    )

    await bob.stop_magic_folder()  # restarted in "finally" below

    try:
        # update the file (so now there's two versions)
        content1 = non_lit_content("one")
        original_folder.child("fluffy").setContent(content1)
        await take_snapshot(alice, "internal", "fluffy")
        await twisted_sleep(reactor, 5)

    finally:
        await bob.start_magic_folder()

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await await_file_contents(
        recover_folder.child("fluffy").path,
        content1,
        timeout=25,
    )


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_ancestors(request, reactor, temp_filepath, alice, bob, take_snapshot):
    original_folder = temp_filepath.child("cats")
    recover_folder = temp_filepath.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    await alice.add("ancestor0", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("ancestor0"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = non_lit_content("zero")
    original_folder.child("nyan").setContent(content0)
    await take_snapshot(alice, "ancestor0", "nyan")

    # create the 'ancestor1' magic-folder
    await bob.add("ancestor1", recover_folder.path)

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("ancestor1"))
    request.addfinalizer(cleanup_recovery)

    # add the 'ancestor0' magic-folder as a participant in the
    # 'ancestor1' folder
    alice_cap = Capability.from_string(alice_folders["ancestor0"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await bob.add_participant("ancestor1", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'ancestor1' filesystem
    await await_file_contents(
        recover_folder.child("nyan").path,
        content0,
        timeout=25,
    )

    # update the file in bob's folder
    content1 = non_lit_content("one")
    recover_folder.child("nyan").setContent(content1)
    await take_snapshot(bob, "ancestor1", "nyan")

    await await_file_contents(
        recover_folder.child("nyan").path,
        content1,
        timeout=25,
    )
    await ensure_file_not_created(
        recover_folder.child("nyan.conflict-alice").path,
        timeout=25,
    )

    # update the file in alice's folder
    content2 = non_lit_content("two")
    original_folder.child("nyan").setContent(content2)
    await take_snapshot(alice, "ancestor0", "nyan")

    # Since we made local changes to the file, a change to alice
    # shouldn't overwrite our changes
    await await_file_contents(
        recover_folder.child("nyan").path,
        content1,
        timeout=25,
    )

@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_recover_twice(request, reactor, temp_filepath, alice, bob, edmond, take_snapshot):
    original_folder = temp_filepath.child("cats")
    recover_folder = temp_filepath.child("kitties")
    recover2_folder = temp_filepath.child("mice")
    original_folder.makedirs()
    recover_folder.makedirs()
    recover2_folder.makedirs()

    # add our magic-folder and re-start
    await alice.add("original", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(alice.start_magic_folder())
        pytest_twisted.blockon(alice.leave("original"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = non_lit_content("zero")
    original_folder.child("pussyfoot").setContent(content0)
    await take_snapshot(alice, "original", "pussyfoot")

    await twisted_sleep(reactor, 5)
    await alice.stop_magic_folder()  # restarted on cleanup

    def cleanup_restart_alice():
        pytest_twisted.blockon(alice.start_magic_folder())
    request.addfinalizer(cleanup_restart_alice)

    # create the 'recovery' magic-folder
    await bob.add("recovery", recover_folder.path)
    bob_folders = await bob.list_(True)

    def cleanup_recovery():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(bob.start_magic_folder())
        pytest_twisted.blockon(bob.leave("recovery"))
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = Capability.from_string(alice_folders["original"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await await_file_contents(
        recover_folder.child("pussyfoot").path,
        content0,
        timeout=25,
    )

    # update the file (so now there's two versions)
    content1 = non_lit_content("one")
    recover_folder.child("pussyfoot").setContent(content1)
    await take_snapshot(bob, "recovery", "pussyfoot")

    # We shouldn't see this show up as a conflict, since we are newer than
    # alice
    await ensure_file_not_created(
        recover_folder.child("pussyfoot.conflict-alice").path,
        timeout=25,
    )

    await twisted_sleep(reactor, 5)
    await bob.stop_magic_folder()  # restarted on cleanup

    def cleanup_restart_bob():
        pytest_twisted.blockon(bob.start_magic_folder())
    request.addfinalizer(cleanup_restart_bob)

    # create the second 'recovery' magic-folder
    await edmond.add("recovery-2", recover2_folder.path)

    def cleanup_recovery_2():
        pytest_twisted.blockon(edmond.leave("recovery-2"))
    request.addfinalizer(cleanup_recovery_2)

    # add the 'recovery' magic-folder as a participant in the
    # 'recovery-2' folder
    bob_cap = Capability.from_string(bob_folders["recovery"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await edmond.add_participant("recovery-2", "bob", bob_cap)

    await await_file_contents(
        recover2_folder.child("pussyfoot").path,
        content1,
        timeout=25,
    )


@pytest.mark.parametrize("take_snapshot", [add_snapshot, scan_folder])
@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_unscanned_conflict(request, reactor, temp_filepath, alice, bob, take_snapshot):
    """
    If we make a change to a local file and a change to the same file on a
    peer, it is detected as a conflict, even if before we take a local snapshot
    of it.
    """
    original_folder = temp_filepath.child("cats")
    recover_folder = temp_filepath.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    await alice.add("original", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(alice.start_magic_folder())
        pytest_twisted.blockon(alice.leave("original"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = non_lit_content("zero")
    original_folder.child("cheshire").setContent(content0)
    await take_snapshot(alice, "original", "cheshire")

    # create the 'recovery' magic-folder
    await bob.add("recovery", recover_folder.path)

    def cleanup_recovery():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(bob.start_magic_folder())
        pytest_twisted.blockon(bob.leave("recovery"))
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = Capability.from_string(alice_folders["original"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await await_file_contents(
        recover_folder.child("cheshire").path,
        content0,
        timeout=10,
    )

    content1 = non_lit_content("one")
    recover_folder.child("cheshire").setContent(content1)

    content2 = non_lit_content("two")
    original_folder.child("cheshire").setContent(content2)
    await take_snapshot(alice, "original", "cheshire")

    await await_file_contents(
        recover_folder.child("cheshire.conflict-alice").path,
        content2,
        timeout=10,
    )
    await await_file_contents(
        recover_folder.child("cheshire").path,
        content1,
        timeout=10,
    )


@pytest.mark.parametrize("take_snapshot", [add_snapshot, scan_folder])
@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_unscanned_vs_old(request, reactor, temp_filepath, alice, bob, take_snapshot):
    """
    If we make a change to a local file, it is not detected as a conflict.
    """
    original_folder = temp_filepath.child("cats")
    recover_folder = temp_filepath.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    await alice.add("original", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(alice.start_magic_folder())
        pytest_twisted.blockon(alice.leave("original"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = non_lit_content("zero")
    original_folder.child("claude").setContent(content0)
    await take_snapshot(alice, "original", "claude")

    # create the 'recovery' magic-folder
    await bob.add("recovery", recover_folder.path)

    def cleanup_recovery():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(bob.start_magic_folder())
        pytest_twisted.blockon(bob.leave("recovery"))
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = Capability.from_string(alice_folders["original"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await await_file_contents(
        recover_folder.child("claude").path,
        content0,
        timeout=10,
    )

    content1 = non_lit_content("one")
    recover_folder.child("claude").setContent(content1)

    await ensure_file_not_created(
        recover_folder.child("claude.conflict-alice").path,
        timeout=10,
    )
    await await_file_contents(
        recover_folder.child("claude").path,
        content1,
        timeout=10,
    )


@pytest.mark.parametrize("take_snapshot", [add_snapshot, scan_folder])
@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_delete(request, reactor, temp_filepath, alice, bob, take_snapshot):
    """
    A delete to a local file is stored and synchronized
    """
    original_folder = temp_filepath.child("cats")
    recover_folder = temp_filepath.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    await alice.add("original", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(alice.start_magic_folder())
        pytest_twisted.blockon(alice.leave("original"))
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = non_lit_content("zero")
    original_folder.child("jerry").setContent(content0)
    await take_snapshot(alice, "original", "jerry")

    # create the 'recovery' magic-folder
    await bob.add("recovery", recover_folder.path)

    def cleanup_recovery():
        # Maybe start the service, so we can remove the folder.
        pytest_twisted.blockon(bob.start_magic_folder())
        pytest_twisted.blockon(bob.leave("recovery"))
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = Capability.from_string(alice_folders["original"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await await_file_contents(
        recover_folder.child("jerry").path,
        content0,
        timeout=10,
    )

    # delete the file
    original_folder.child("jerry").remove()
    await take_snapshot(alice, "original", "jerry")

    await await_file_vanishes(
        recover_folder.child("jerry").path,
        timeout=20,
    )
