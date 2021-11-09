from __future__ import absolute_import, division, print_function, unicode_literals

import pytest_twisted
from twisted.internet.error import ProcessTerminated
from twisted.internet.defer import (
    DeferredList,
)

from magic_folder.util.capabilities import to_readonly_capability

from .util import await_file_contents, ensure_file_not_created


@pytest_twisted.inlineCallbacks
def test_add(request, reactor, alice):
    """
    'magic-folder add' happy-path works
    """

    yield alice.add(
        "test",
        alice.magic_directory,
        author="laptop",
    )

    def cleanup():
        pytest_twisted.blockon(alice.leave("test"))

    request.addfinalizer(cleanup)

    config = yield alice.show_config()

    assert "test" in config["magic_folders"]
    mf_config = config["magic_folders"]["test"]
    assert mf_config["name"] == "test"
    assert mf_config["author_name"] == "laptop"
    expected_keys = ["stash_path", "author_private_key"]
    assert all(
        k in mf_config
        for k in expected_keys
    )


@pytest_twisted.inlineCallbacks
def test_leave(request, reactor, temp_filepath, alice, bob):
    """
    After leaving a magic folder, its contents are no longer
    synced.
    """
    magic = temp_filepath
    original_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    original_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("original", original_folder.path)
    alice_folders = yield alice.list_(True)

    def cleanup_original():
        pytest_twisted.blockon(alice.leave("original"))

    request.addfinalizer(cleanup_original)

    content0 = "zero\n" * 1000
    original_folder.child("grumpy").setContent(content0)
    yield alice.add_snapshot("original", "grumpy")

    yield bob.add("recovery", recover_folder.path)

    def cleanup_recovery():
        try:
            pytest_twisted.blockon(bob.leave("recovery"))
        except ProcessTerminated:
            pass  # Already left

    request.addfinalizer(cleanup_recovery)

    # add the 'original' magic-folder as a participant in the
    # 'recovery' folder
    alice_cap = to_readonly_capability(alice_folders["original"]["upload_dircap"])
    yield bob.add_participant("recovery", "alice", alice_cap)

    await_file_contents(
        recover_folder.child("grumpy").path,
        content0,
        timeout=25,
    )

    yield bob.leave("recovery")

    content1 = "one\n" * 1000
    original_folder.child("sylvester").setContent(content1)
    yield alice.add_snapshot("original", "sylvester")

    ensure_file_not_created(
        recover_folder.child("sylvester").path,
        timeout=25,
    )


@pytest_twisted.inlineCallbacks
def test_leave_many(request, reactor, temp_filepath, alice, bob):
    """
    Many magic-folders can be added and left in rapid succession

    See also https://github.com/LeastAuthority/magic-folder/issues/587
    """
    magic = temp_filepath

    names = [
        "folder_{}".format(x)
        for x in range(10)
    ]

    for name in names:
        folder = magic.child(name)
        folder.makedirs()

        yield alice.add(name, folder.path)

    alice_folders = yield alice.list_(True)
    assert set(alice_folders.keys()) == set(names)

    # try and ensure that the folders are "doing some work" by adding
    # files to them all (sizes are in KiB)
    fake_files = (
        ('zero', 100),  # 100K
        ('one', 10000), # 10M
    )
    for fname, size in fake_files:
        for name in names:
            with magic.child(name).child(fname).open("wb") as f:
                for _ in range(size):
                    f.write("xxxxxxx\n" * (1024 // 8))

    # initiate a scan on them all
    scans = []
    for name in names:
        print("scan: {}".format(name))
        scans.append(alice.scan_folder(name))
    res = yield DeferredList(scans)
    print(res)

    for name in names:
        print("leaving", name)
        yield alice.leave(name)
