from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Testing synchronizing files between participants
"""

from tempfile import (
    mkdtemp,
)

from twisted.python.filepath import (
    FilePath,
)
import pytest_twisted

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

    @pytest_twisted.inlineCallbacks
    def cleanup_original():
        yield alice.leave("original")
        yield alice.restart_magic_folder()
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
    original_folder.child("sylvester").setContent(content2)
    yield alice.add_snapshot("original", "sylvester")

    # the new content should appear in the 'recovery' folder
    await_file_contents(
        recover_folder.child("sylvester").path,
        content2,
    )
