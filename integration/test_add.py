import pytest_twisted
from twisted.internet.error import ProcessTerminated
from twisted.internet.defer import (
    DeferredList,
)
from eliot.twisted import (
    inline_callbacks,
)

from magic_folder.util.capabilities import (
    Capability,
)

from .util import await_file_contents, ensure_file_not_created


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_add(request, reactor, alice):
    """
    'magic-folder add' happy-path works
    """

    await alice.add(
        "test",
        alice.magic_directory,
        author="laptop",
    )

    def cleanup():
        pytest_twisted.blockon(alice.leave("test"))

    request.addfinalizer(cleanup)

    config = await alice.show_config()
    print("CONFIG", config)

    assert "test" in config["magic_folders"]
    mf_config = config["magic_folders"]["test"]
    assert mf_config["name"] == "test"
    assert mf_config["author_name"] == "laptop"
    expected_keys = ["stash_path", "author_private_key"]
    assert all(
        k in mf_config
        for k in expected_keys
    )


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_leave(request, reactor, temp_filepath, alice, bob):
    """
    After leaving a magic folder, its contents are no longer
    synced.
    """
    magic = temp_filepath
    original_folder = magic.child("cats")
    recover_folder = magic.child("cats_recover")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    await alice.add("original", original_folder.path)
    alice_folders = await alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("original"))

    request.addfinalizer(cleanup_original)

    content0 = b"zero\n" * 1000
    original_folder.child("grumpy").setContent(content0)
    await alice.add_snapshot("original", "grumpy")

    await bob.add("recovery", recover_folder.path)

    def cleanup_recovery():
        try:
            pytest_twisted.blockon(bob.leave("recovery"))
        except ProcessTerminated:
            pass  # Already left

    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = Capability.from_string(alice_folders["original"]["upload_dircap"]).to_readonly().danger_real_capability_string()
    await bob.add_participant("recovery", "alice", alice_cap)

    await await_file_contents(
        recover_folder.child("grumpy").path,
        content0,
        timeout=25,
    )

    await bob.leave("recovery")

    content1 = b"one\n" * 1000
    original_folder.child("sylvester").setContent(content1)
    await alice.add_snapshot("original", "sylvester")

    await ensure_file_not_created(
        recover_folder.child("sylvester").path,
        timeout=25,
    )


@inline_callbacks
@pytest_twisted.ensureDeferred
async def test_leave_many(request, reactor, temp_filepath, alice):
    """
    Many magic-folders can be added and left in rapid succession

    See also https://github.com/LeastAuthority/magic-folder/issues/587
    """
    names = [
        "folder_{}".format(x)
        for x in range(10)
    ]

    for name in names:
        folder = temp_filepath.child(name)
        folder.makedirs()

        await alice.add(name, folder.path)

    alice_folders = await alice.list_(True)
    assert set(alice_folders.keys()) == set(names)

    # try and ensure that the folders are "doing some work" by adding
    # files to them all (sizes are in KiB)
    fake_files = (
        ('zero', 100),  # 100K
        ('one', 10000), # 10M
    )
    for fname, size in fake_files:
        for name in names:
            with temp_filepath.child(name).child(fname).open("wb") as f:
                for _ in range(size):
                    f.write(b"xxxxxxx\n" * (1024 // 8))

    # initiate a scan on them all
    scans = await DeferredList([
        alice.scan_folder(name)
        for name in names
    ])
    assert all(ok for ok, _ in scans), "at least one scan failed"

    leaves = await DeferredList([
        alice.leave(name)
        for name in names
    ])
    assert all(ok for ok, _ in leaves), "at least one leave() failed"

    alice_folders = await alice.list_(True)
    assert not alice_folders, "should be zero folders"
