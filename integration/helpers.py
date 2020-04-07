from io import BytesIO

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from eliot import (
    start_action,
)

from magic_folder.cli import (
    MagicFolderCommand,
    do_magic_folder,
)


@inlineCallbacks
def _pair_magic_folder(reactor, alice_invite, alice, bob):
    print("Joining bob to magic-folder")
    yield _command(
        "--node-directory", bob.node_directory,
        "join",
        "--poll-interval", "1",
        alice_invite,
        bob.magic_directory,
    )

    # before magic-folder works, we have to stop and restart (this is
    # crappy for the tests -- can we fix it in magic-folder?)
    yield bob.restart_magic_folder()

    returnValue((alice.magic_directory, bob.magic_directory))


@inlineCallbacks
def _generate_invite(reactor, inviter, invitee_name):
    """
    Create a new magic-folder invite.

    :param MagicFolderEnabledNode inviter: the node who will generate the invite

    :param str invitee: the name of the node who will be invited
    """
    action_prefix = u"integration:{}:magic_folder".format(inviter.name)
    with start_action(action_type=u"{}:create".format(action_prefix)):
        print("Creating magic-folder for {}".format(inviter.node_directory))
        yield _command(
            "--node-directory", inviter.node_directory,
            "create",
            "--poll-interval", "2", "magik:", inviter.name, inviter.magic_directory,
        )

    with start_action(action_type=u"{}:invite".format(action_prefix)) as a:
        print("Inviting '{}' to magic-folder for {}".format(invitee_name, inviter.node_directory))
        invite = yield _command(
            "--node-directory", inviter.node_directory,
            "invite",
            "magik:", invitee_name,
        )
        a.add_success_fields(invite=invite)

    with start_action(action_type=u"{}:restart".format(action_prefix)):
        # before magic-folder works, we have to stop and restart (this is
        # crappy for the tests -- can we fix it in magic-folder?)
        yield inviter.restart_magic_folder()
    returnValue(invite)



@inlineCallbacks
def _command(*args):
    """
    Runs a single magic-folder command with the given arguments as CLI
    arguments to `magic-folder`. If the exit-code is not 0, an
    exception is raised.

    :returns: stdout
    """
    o = MagicFolderCommand()
    o.stdout = BytesIO()
    o.parseOptions(args)
    return_value = yield do_magic_folder(o)
    assert 0 == return_value
    returnValue(o.stdout.getvalue())
