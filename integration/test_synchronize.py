"""
Testing synchronizing files between participants
"""

from __future__ import (
    unicode_literals,
)

from json import (
    loads,
)
from tempfile import mkdtemp, mktemp

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    deferLater,
)
import pytest_twisted
from hypothesis.strategies import (
    just,
    one_of,
    sampled_from,
    booleans,
    characters,
    text,
    lists,
    builds,
    binary,
    integers,
    floats,
    fixed_dictionaries,
    dictionaries,
)

from magic_folder.util.capabilities import (
    to_readonly_capability,
)
from .util import (
    await_file_contents,
)


@pytest_twisted.inlineCallbacks
def test_create_then_recover(request, reactor, temp_dir, alice, bob):
    """
    Test a version of the expected 'recover' workflow:
    - make a magic-folder on device 'alice'
    - put a snapshot in it

    - recovery workflow:
    - create a new magic-folder on device 'bob'
    - add the 'alice' Personal DMD a participant
    - the snapshot should appear

    - bonus: the old device is found
    - update that snapshot in the original (so it has one parent)
    - the update should appear on recovery device
    """

    # "alice" contains the 'original' magic-folder
    # "bob" contains the 'recovery' magic-folder
    magic = FilePath(mkdtemp())
    orig_folder = magic.child("cats")
    recover_folder = magic.child("kitties")
    orig_folder.makedirs()
    recover_folder.makedirs()

    # add our magic-folder and re-start
    yield alice.add("original", orig_folder.path)
    yield alice.restart_magic_folder()
    alice_folders = yield alice.list_(True)

    @pytest_twisted.inlineCallbacks
    def cleanup_original():
        yield alice.leave("original")
        yield alice.restart_magic_folder()
    request.addfinalizer(cleanup_original)

    # put a file in our folder
    content0 = "zero\n" * 1000
    with orig_folder.child("sylvester").open("w") as f:
        f.write(content0)
    yield alice.add_snapshot("original", "sylvester")

    # update the file so there's two versions
    content1 = "one\n" * 1000
    with orig_folder.child("sylvester").open("w") as f:
        f.write(content1)
    yield alice.add_snapshot("original", "sylvester")

    # create the 'recovery' magic-folder
    yield bob.add("recovery", recover_folder.path)
    yield bob.restart_magic_folder()

    @pytest_twisted.inlineCallbacks
    def cleanup_recovery():
        yield bob.leave("recovery")
        yield bob.restart_magic_folder()
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
    )

    # in the (ideally rare) case that the old device is found *and* a
    # new snapshot is uploaded, we put an update into the 'original'
    # folder. This also tests the normal 'update' flow as well.
    content2 = "two\n" * 1000
    with orig_folder.child("sylvester").open("w") as f:
        f.write(content2)
    yield alice.add_snapshot("original", "sylvester")

    # the new content should appear in the 'recovery' folder
    await_file_contents(
        recover_folder.child("sylvester").path,
        content2,
    )
