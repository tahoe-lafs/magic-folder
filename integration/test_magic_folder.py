import time
import shutil
import json
from os import mkdir, unlink, utime
from os.path import join, exists, getmtime
from functools import (
    partial,
)

import pytest_twisted

from eliot import (
    start_action,
    write_traceback,
)

from twisted.internet.defer import (
    returnValue,
)

from twisted.web.client import (
    Agent,
    readBody,
)

from twisted.python.filepath import (
    FilePath,
)

from treq.client import (
    HTTPClient,
)

from magic_folder.magic_folder import (
    load_magic_folders,
)
from magic_folder.cli import (
    MagicFolderCommand,
    do_magic_folder,
    poll,
)
from .util import (
    MagicFolderEnabledNode,
    _generate_invite,
    _pair_magic_folder,
    _command,
    await_file_contents,
    await_files_exist,
    await_file_vanishes,
)

from magic_folder.status import (
    status,
)

# see "conftest.py" for the fixtures (e.g. "magic_folder")

def test_eliot_logs_are_written(alice, bob, temp_dir):
    # The integration test configuration arranges for this logging
    # configuration.  Verify it actually does what we want.
    #
    # The alice and bob arguments looks unused but they actually tell pytest
    # to set up all the magic-folder stuff.  The assertions here are about
    # side-effects of that setup.
    assert exists(join(temp_dir, "alice", "logs", "eliot.json"))
    assert exists(join(temp_dir, "bob", "logs", "eliot.json"))


def test_alice_writes_bob_receives(magic_folder):
    alice_dir, bob_dir = magic_folder

    with open(join(alice_dir, "first_file"), "w") as f:
        f.write("alice wrote this")

    await_file_contents(join(bob_dir, "first_file"), "alice wrote this")
    return


def test_alice_writes_bob_receives_multiple(magic_folder):
    """
    When Alice does a series of updates, Bob should just receive them
    with no .backup or .conflict files being produced.
    """
    alice_dir, bob_dir = magic_folder

    unwanted_files = [
        join(bob_dir, "multiple.backup"),
        join(bob_dir, "multiple.conflict")
    ]

    # first update
    with open(join(alice_dir, "multiple"), "w") as f:
        f.write("alice wrote this")

    await_file_contents(
        join(bob_dir, "multiple"), "alice wrote this",
        error_if=unwanted_files,
    )

    # second update
    with open(join(alice_dir, "multiple"), "w") as f:
        f.write("someone changed their mind")

    await_file_contents(
        join(bob_dir, "multiple"), "someone changed their mind",
        error_if=unwanted_files,
    )

    # third update
    with open(join(alice_dir, "multiple"), "w") as f:
        f.write("absolutely final version ship it")

    await_file_contents(
        join(bob_dir, "multiple"), "absolutely final version ship it",
        error_if=unwanted_files,
    )

    # forth update, but both "at once" so one should conflict
    time.sleep(2)
    with open(join(alice_dir, "multiple"), "w") as f:
        f.write("okay one more attempt")
    with open(join(bob_dir, "multiple"), "w") as f:
        f.write("...but just let me add")

    bob_conflict = join(bob_dir, "multiple.conflict")
    alice_conflict = join(alice_dir, "multiple.conflict")

    found = await_files_exist([
        bob_conflict,
        alice_conflict,
    ])

    assert len(found) > 0, "Should have found a conflict"
    print("conflict found (as expected)")


def test_alice_writes_bob_receives_old_timestamp(magic_folder):
    alice_dir, bob_dir = magic_folder
    fname = join(alice_dir, "ts_file")
    ts = time.time() - (60 * 60 * 36)  # 36 hours ago

    with open(fname, "w") as f:
        f.write("alice wrote this")
    utime(fname, (time.time(), ts))

    fname = join(bob_dir, "ts_file")
    await_file_contents(fname, "alice wrote this")
    # make sure the timestamp is correct
    assert int(getmtime(fname)) == int(ts)
    return


def test_bob_writes_alice_receives(magic_folder):
    alice_dir, bob_dir = magic_folder

    with open(join(bob_dir, "second_file"), "w") as f:
        f.write("bob wrote this")

    await_file_contents(join(alice_dir, "second_file"), "bob wrote this")
    return


def test_alice_deletes(magic_folder):
    # alice writes a file, waits for bob to get it and then deletes it.
    alice_dir, bob_dir = magic_folder

    with open(join(alice_dir, "delfile"), "w") as f:
        f.write("alice wrote this")

    await_file_contents(join(bob_dir, "delfile"), "alice wrote this")

    # bob has the file; now alices deletes it
    unlink(join(alice_dir, "delfile"))

    # bob should remove his copy, but preserve a backup
    await_file_vanishes(join(bob_dir, "delfile"))
    await_file_contents(join(bob_dir, "delfile.backup"), "alice wrote this")
    return


def test_alice_creates_bob_edits(magic_folder):
    alice_dir, bob_dir = magic_folder

    # alice writes a file
    with open(join(alice_dir, "editfile"), "w") as f:
        f.write("alice wrote this")

    await_file_contents(join(bob_dir, "editfile"), "alice wrote this")

    # now bob edits it
    with open(join(bob_dir, "editfile"), "w") as f:
        f.write("bob says foo")

    await_file_contents(join(alice_dir, "editfile"), "bob says foo")


def test_bob_creates_sub_directory(magic_folder):
    alice_dir, bob_dir = magic_folder

    # bob makes a sub-dir, with a file in it
    mkdir(join(bob_dir, "subdir"))
    with open(join(bob_dir, "subdir", "a_file"), "w") as f:
        f.write("bob wuz here")

    # alice gets it
    await_file_contents(join(alice_dir, "subdir", "a_file"), "bob wuz here")

    # now bob deletes it again
    shutil.rmtree(join(bob_dir, "subdir"))

    # alice should delete it as well
    await_file_vanishes(join(alice_dir, "subdir", "a_file"))
    # i *think* it's by design that the subdir won't disappear,
    # because a "a_file.backup" should appear...
    await_file_contents(join(alice_dir, "subdir", "a_file.backup"), "bob wuz here")


def test_bob_creates_alice_deletes_bob_restores(magic_folder):
    alice_dir, bob_dir = magic_folder

    # bob creates a file
    with open(join(bob_dir, "boom"), "w") as f:
        f.write("bob wrote this")

    await_file_contents(
        join(alice_dir, "boom"),
        "bob wrote this"
    )

    # alice deletes it (so bob should as well .. but keep a backup)
    unlink(join(alice_dir, "boom"))
    await_file_vanishes(join(bob_dir, "boom"))
    assert exists(join(bob_dir, "boom.backup"))

    # bob restore it, with new contents
    unlink(join(bob_dir, "boom.backup"))
    with open(join(bob_dir, "boom"), "w") as f:
        f.write("bob wrote this again, because reasons")

    await_file_contents(
        join(alice_dir, "boom"),
        "bob wrote this again, because reasons",
    )


def test_bob_creates_alice_deletes_alice_restores(magic_folder):
    alice_dir, bob_dir = magic_folder

    # bob creates a file
    with open(join(bob_dir, "boom2"), "w") as f:
        f.write("bob wrote this")

    await_file_contents(
        join(alice_dir, "boom2"),
        "bob wrote this"
    )

    # alice deletes it (so bob should as well)
    unlink(join(alice_dir, "boom2"))
    await_file_vanishes(join(bob_dir, "boom2"))

    # alice restore it, with new contents
    with open(join(alice_dir, "boom2"), "w") as f:
        f.write("alice re-wrote this again, because reasons")

    await_file_contents(
        join(bob_dir, "boom2"),
        "alice re-wrote this again, because reasons"
    )


def test_bob_conflicts_with_alice_fresh(magic_folder):
    # both alice and bob make a file at "the same time".
    alice_dir, bob_dir = magic_folder

    # either alice or bob will "win" by uploading to the DMD first.
    with open(join(bob_dir, 'alpha'), 'w') as f0, open(join(alice_dir, 'alpha'), 'w') as f1:
        f0.write("this is bob's alpha\n")
        f1.write("this is alice's alpha\n")

    # there should be conflicts
    _bob_conflicts_alice_await_conflicts('alpha', alice_dir, bob_dir)


def test_bob_conflicts_with_alice_preexisting(magic_folder):
    # both alice and bob edit a file at "the same time" (similar to
    # above, but the file already exists before the edits)
    alice_dir, bob_dir = magic_folder

    # have bob create the file
    with open(join(bob_dir, 'beta'), 'w') as f:
        f.write("original beta (from bob)\n")
    await_file_contents(join(alice_dir, 'beta'), "original beta (from bob)\n")

    # both alice and bob now have a "beta" file, at version 0

    # either alice or bob will "win" by uploading to the DMD first
    # (however, they should both detect a conflict)
    with open(join(bob_dir, 'beta'), 'w') as f:
        f.write("this is bob's beta\n")
    with open(join(alice_dir, 'beta'), 'w') as f:
        f.write("this is alice's beta\n")

    # both alice and bob should see a conflict
    _bob_conflicts_alice_await_conflicts("beta", alice_dir, bob_dir)


def _bob_conflicts_alice_await_conflicts(name, alice_dir, bob_dir):
    """
    shared code between _fresh and _preexisting conflict test
    """
    found = await_files_exist(
        [
            join(bob_dir, '{}.conflict'.format(name)),
            join(alice_dir, '{}.conflict'.format(name)),
        ],
    )

    assert len(found) >= 1, "should be at least one conflict"
    assert open(join(bob_dir, name), 'r').read() == "this is bob's {}\n".format(name)
    assert open(join(alice_dir, name), 'r').read() == "this is alice's {}\n".format(name)

    alice_conflict = join(alice_dir, '{}.conflict'.format(name))
    bob_conflict = join(bob_dir, '{}.conflict'.format(name))
    if exists(bob_conflict):
        assert open(bob_conflict, 'r').read() == "this is alice's {}\n".format(name)
    if exists(alice_conflict):
        assert open(alice_conflict, 'r').read() == "this is bob's {}\n".format(name)


@pytest_twisted.inlineCallbacks
def test_edmond_uploads_then_restarts(reactor, request, temp_dir, introducer_furl, flog_gatherer, edmond):
    """
    ticket 2880: if a magic-folder client uploads something, then
    re-starts a spurious .backup file should not appear
    """
    name = "edmond"
    node_dir = join(temp_dir, name)

    magic_folder = join(temp_dir, 'magic-edmond')
    mkdir(magic_folder)

    with start_action(action_type=u"integration:edmond:magic_folder:create"):
        o = MagicFolderCommand()
        o.parseOptions([
            "--node-directory", node_dir,
            "create",
            "--poll-interval", "2",
            "magik:", "edmond_magic", magic_folder,
        ])
        rc = yield do_magic_folder(o)
        assert 0 == rc

        # to actually-start the magic-folder we have to re-start
        yield edmond.restart_magic_folder()

    # add a thing to the magic-folder
    with open(join(magic_folder, "its_a_file"), "w") as f:
        f.write("edmond wrote this")

    # fixme, do status-update attempts in a loop below
    time.sleep(5)

    # let it upload; poll the HTTP magic-folder status API until it is
    # uploaded
    uploaded = False
    for _ in range(10):
        try:
            treq = HTTPClient(Agent(reactor))
            mf = yield status(unicode('default', 'utf-8'),
                              FilePath(edmond.node_directory),
                              treq)
            if mf.folder_status[0]['status'] == u'success' and \
               mf.local_files.get(u'its_a_file', None) is not None:
                uploaded = True
                break
        except Exception:
            write_traceback()
            time.sleep(1)

    assert uploaded, "expected to upload 'its_a_file'"

    # re-starting edmond right now would previously have triggered the 2880 bug
    # kill edmond
    yield edmond.restart_magic_folder()

    # XXX how can we say for sure if we've waited long enough? look at
    # tail of logs for magic-folder ... somethingsomething?
    print("waiting 20 seconds to see if a .backup appears")
    for _ in range(20):
        assert exists(join(magic_folder, "its_a_file"))
        assert not exists(join(magic_folder, "its_a_file.backup"))
        time.sleep(1)


@pytest_twisted.inlineCallbacks
def test_alice_adds_files_while_bob_is_offline(reactor, request, temp_dir, magic_folder, bob):
    """
    Alice can add new files to a magic folder while Bob is offline.  When Bob
    comes back online his copy is updated to reflect the new files.
    """
    alice_magic_dir, bob_magic_dir = magic_folder
    alice_node_dir = join(temp_dir, "alice")

    # Take Bob offline.
    yield bob.stop_magic_folder()

    # Create a couple files in Alice's local directory.
    some_files = list(
        (name * 3) + ".added-while-offline"
        for name
        in "xyz"
    )
    for name in some_files:
        with open(join(alice_magic_dir, name), "w") as f:
            f.write(name + " some content")

    agent = Agent(reactor)

    upload_dircap = load_magic_folders(alice_node_dir)["default"]["upload_dircap"]

    # Alice's tahoe-lafs web api
    uri = "http://127.0.0.1:9980/uri/{}?t=json".format(upload_dircap)

    @pytest_twisted.inlineCallbacks
    def good_remote_state(agent):
        response = yield agent.request(
            b"GET",
            uri,
        )
        if response.code != 200:
            returnValue(False)

        files = json.loads((yield readBody(response)))
        print(files)
        returnValue(True)

    yield poll("good-remote-state", partial(good_remote_state, agent), reactor)

    # Start Bob up again
    yield bob.start_magic_folder()

    yield await_files_exist(
        list(
            join(bob_magic_dir, name)
            for name
            in some_files
        ),
        await_all=True,
    )
    # Let it settle.  It would be nicer to have a readable status output we
    # could query.  Parsing the current text format is more than I want to
    # deal with right now.
    time.sleep(1.0)
    conflict_files = list(name + ".conflict" for name in some_files)
    assert all(
        list(
            not exists(join(bob_magic_dir, name))
            for name
            in conflict_files
        ),
    )


@pytest_twisted.inlineCallbacks
def test_francis_leaves(reactor, request, introducer_furl, flog_gatherer, temp_dir, magic_folder, alice, bob):
    """
    Set up a magic-folder with francis + gloria; after francis leaves
    she shouldn't receive any more updates.
    """
    with start_action(action_type=u"integration:francis:magic_folder:create"):
        francis = yield MagicFolderEnabledNode.create(
            reactor,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            "francis",
            tahoe_web_port="tcp:9986:interface=localhost",
            magic_folder_web_port="tcp:19986:interface=localhost",
            storage=False,
        )

    with start_action(action_type=u"integration:gloria:magic_folder:create"):
        gloria = yield MagicFolderEnabledNode.create(
            reactor,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            "gloria",
            tahoe_web_port="tcp:9987:interface=localhost",
            magic_folder_web_port="tcp:19987:interface=localhost",
            storage=False,
        )

    # create a magic folder and invite gloria
    invite = yield _generate_invite(reactor, francis, "gloria")
    yield _pair_magic_folder(reactor, invite, francis, gloria)

    # make an update from francis -> gloria
    first_fname = join(francis.magic_directory, "francis_to_gloria")
    with open(first_fname, "w") as f:
        f.write("francis some content")

    # wait for gloria to get it
    yield await_file_contents(
        join(gloria.magic_directory, "francis_to_gloria"),
        "francis some content",
    )

    print("okay, it worked")

    # gloria leaves the magic-folder
    yield _command(
        "--node-directory", gloria.node_directory,
        "leave",
    )

    # make another update from francis -> gloria
    second_fname = join(francis.magic_directory, "francis_to_gloria_2")
    with open(second_fname, "w") as f:
        f.write("francis some MOAR content")

    # gloria should not get it (we want an error)
    try:
        yield await_file_contents(
            join(gloria.magic_directory, "francis_to_gloria_2"),
            "francis some MOAR content",
        )
        assert False, "Gloria got the new file, but shouldn't have"
    except Exception as e:
        print("expected: {}".format(e))
