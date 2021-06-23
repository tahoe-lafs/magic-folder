from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Testing synchronizing files between participants
"""

import time
from tempfile import (
    mkdtemp,
)

from twisted.python.filepath import (
    FilePath,
)
import pytest
import pytest_twisted

from magic_folder.util.capabilities import (
    to_readonly_capability,
)
from .util import (
    await_file_contents,
    ensure_file_not_created,
)


@pytest_twisted.inlineCallbacks
def test_local_snapshots(request, reactor, temp_dir, alice, bob):
    """
    Create several snapshots while our Tahoe client is offline.
    """

    magic = FilePath(mkdtemp())

    # add our magic-folder and re-start
    yield alice.add("local", magic.path)
    yield alice.restart_magic_folder()
    local_cfg = alice.global_config().get_magic_folder("local")

    def cleanup():
        pytest_twisted.blockon(alice.leave("local"))
        pytest_twisted.blockon(alice.restart_magic_folder())
    request.addfinalizer(cleanup)

    # put a file in our folder
    content0 = "zero\n" * 1000
    magic.child("sylvester").setContent(content0)
    yield alice.add_snapshot("local", "sylvester")

    # wait until we've definitely uploaded it
    for _ in range(10):
        time.sleep(1)
        try:
            former_remote = local_cfg.get_remotesnapshot("sylvester")
            break
        except KeyError:
            pass
    x = yield alice.dump_state("local")
    print(x)

    # turn off Tahoe
    alice.pause_tahoe()

    try:
        # add several snapshots
        content1 = "one\n" * 1000
        magic.child("sylvester").setContent(content1)
        yield alice.add_snapshot("local", "sylvester")
        content2 = "two\n" * 1000
        magic.child("sylvester").setContent(content2)
        yield alice.add_snapshot("local", "sylvester")
        content3 = "three\n" * 1000
        magic.child("sylvester").setContent(content3)
        yield alice.add_snapshot("local", "sylvester")

        x = yield alice.dump_state("local")
        print(x)

        assert local_cfg.get_all_localsnapshot_paths() == {"sylvester"}
        snap = local_cfg.get_local_snapshot("sylvester")
        print(snap)
        # we should have 3 snapshots total, each one the parent of the next
        assert len(snap.parents_local) == 1 and \
            len(snap.parents_local[0].parents_local) == 1 and \
            len(snap.parents_local[0].parents_local[0].parents_local) == 0 and \
            len(snap.parents_local[0].parents_local[0].parents_remote) == 1

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
        time.sleep(1)
    assert found, "Expected 'sylvester' to be (only) a remote-snapshot"


@pytest_twisted.inlineCallbacks
def test_create_then_recover(request, reactor, temp_dir, alice, bob):
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
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("original", original_folder.path)
    yield alice.restart_magic_folder()
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("original"))
        pytest_twisted.blockon(alice.restart_magic_folder())
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield alice.add_snapshot("original", "sylvester")

    # update the file (so now there's two versions)
    content1 = "one\n" * 1000
    original_folder.child("sylvester").setContent(content1)
    yield alice.add_snapshot("original", "sylvester")

    # create the 'recovery' magic-folder
    yield bob.add("recovery", recover_folder.path)
    yield bob.restart_magic_folder()

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("recovery"))
        pytest_twisted.blockon(bob.restart_magic_folder())
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = to_readonly_capability(alice_folders["original"]["upload_dircap"])
    yield bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=15,
    )

    # in the (ideally rare) case that the old device is found *and* a
    # new snapshot is uploaded, we put an update into the 'original'
    # folder. This also tests the normal 'update' flow as well.
    content2 = "two\n" * 1000
    original_folder.child("sylvester").setContent(content2)
    yield alice.add_snapshot("original", "sylvester")

    # the new content should appear in the 'recovery' folder
    await_file_contents(
        recover_folder.child("sylvester").path,
        content2,
    )


@pytest_twisted.inlineCallbacks
def test_internal_inconsistency(request, reactor, temp_dir, alice, bob):
    #FIXME needs docstring
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("internal", original_folder.path)
    yield alice.restart_magic_folder()
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("internal"))
        pytest_twisted.blockon(alice.restart_magic_folder())
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield alice.add_snapshot("internal", "sylvester")

    # create the 'rec' magic-folder
    yield bob.add("rec", recover_folder.path)
    yield bob.restart_magic_folder()

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("rec"))
        pytest_twisted.blockon(bob.restart_magic_folder())
    request.addfinalizer(cleanup_recovery)

    # add the 'internal' magic-folder as a participant in the
    # 'rec' folder
    alice_cap = to_readonly_capability(alice_folders["internal"]["upload_dircap"])
    yield bob.add_participant("rec", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content0,
        timeout=15,
    )

    yield bob.stop_magic_folder()

    # update the file (so now there's two versions)
    content1 = "one\n" * 1000
    original_folder.child("sylvester").setContent(content1)
    yield alice.add_snapshot("internal", "sylvester")

    time.sleep(2)

    yield bob.start_magic_folder()

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=15,
    )


@pytest_twisted.inlineCallbacks
def test_ancestors(request, reactor, temp_dir, alice, bob):
    # FIXME needs docstring
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("ancestor0", original_folder.path)
    yield alice.restart_magic_folder()
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("ancestor0"))
        pytest_twisted.blockon(alice.restart_magic_folder())
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield alice.add_snapshot("ancestor0", "sylvester")

    # create the 'ancestor1' magic-folder
    yield bob.add("ancestor1", recover_folder.path)
    yield bob.restart_magic_folder()

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("ancestor1"))
        pytest_twisted.blockon(bob.restart_magic_folder())
    request.addfinalizer(cleanup_recovery)

    # add the 'ancestor0' magic-folder as a participant in the
    # 'ancestor1' folder
    alice_cap = to_readonly_capability(alice_folders["ancestor0"]["upload_dircap"])
    yield bob.add_participant("ancestor1", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'ancestor1' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content0,
        timeout=15,
    )

    # update the file in bob's folder
    content1 = "one\n" * 1000
    recover_folder.child("sylvester").setContent(content1)
    yield bob.add_snapshot("ancestor1", "sylvester")

    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=15,
    )
    ensure_file_not_created(
        recover_folder.child("sylvester.conflict-alice").path,
        timeout=15,
    )

    # update the file in alice's folder
    content2 = "two\n" * 1000
    original_folder.child("sylvester").setContent(content2)
    yield alice.add_snapshot("ancestor0", "sylvester")

    # Since we made local changes to the file, a change to alice
    # shouldn't overwrite our changes
    await_file_contents(
        recover_folder.child("sylvester").path,
        content1,
        timeout=15,
    )


@pytest_twisted.inlineCallbacks
def test_recover_twice(request, reactor, temp_dir, alice, bob, edmond):
    #XXX FIXME needs docstring
    magic = FilePath(mkdtemp())
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    recover2_folder = magic.child("mice")
    original_folder.makedirs()
    recover_folder.makedirs()
    recover2_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("original", original_folder.path)
    yield alice.restart_magic_folder()
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("original"))
        pytest_twisted.blockon(alice.restart_magic_folder())
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    original_folder.child("sylvester").setContent(content0)
    yield alice.add_snapshot("original", "sylvester")

    time.sleep(5)
    yield alice.stop_magic_folder()

    # create the 'recovery' magic-folder
    yield bob.add("recovery", recover_folder.path)
    yield bob.restart_magic_folder()
    bob_folders = yield bob.list_(True)

    def cleanup_recovery():
        pytest_twisted.blockon(bob.leave("recovery"))
        pytest_twisted.blockon(bob.restart_magic_folder())
    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = to_readonly_capability(alice_folders["original"]["upload_dircap"])
    yield bob.add_participant("recovery", "alice", alice_cap)

    # we should now see the only Snapshot we have in the folder appear
    # in the 'recovery' filesystem
    await_file_contents(
        recover_folder.child("sylvester").path,
        content0,
        timeout=15,
    )

    # update the file (so now there's two versions)
    content1 = "one\n" * 1000
    recover_folder.child("sylvester").setContent(content1)
    yield bob.add_snapshot("recovery", "sylvester")

    # We shouldn't see this show up as a conflict, since we are newer than
    # alice
    ensure_file_not_created(
        recover_folder.child("sylvester.conflict-alice").path,
        timeout=15,
    )

    time.sleep(5)
    yield bob.stop_magic_folder()

    # create the second 'recovery' magic-folder
    yield edmond.add("recovery-2", recover2_folder.path)
    yield edmond.restart_magic_folder()

    def cleanup_recovery():
        pytest_twisted.blockon(edmond.leave("recovery-2"))
        pytest_twisted.blockon(edmond.restart_magic_folder())
    request.addfinalizer(cleanup_recovery)

    # add the 'recovery' magic-folder as a participant in the
    # 'recovery-2' folder
    bob_cap = to_readonly_capability(bob_folders["recovery"]["upload_dircap"])
    yield edmond.add_participant("recovery-2", "bob", bob_cap)

    await_file_contents(
        recover2_folder.child("sylvester").path,
        content1,
        timeout=15,
    )
