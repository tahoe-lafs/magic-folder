import sys
from io import BytesIO
from os.path import join
from functools import partial

import attr

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from eliot import (
    Message,
    start_action,
)
from eliot.twisted import (
    DeferredContext,
)

from magic_folder.cli import (
    MagicFolderCommand,
    do_magic_folder,
)
from .util import (
    await_client_ready,
    _create_node,
    _cleanup_tahoe_process,
    _MagicTextProtocol,
)


@attr.s
class MagicFolderEnabledNode(object):
    """
    Keep track of a Tahoe-LAFS node child process and an associated
    magic-folder child process.

    :ivar IProcessTransport tahoe: The Tahoe-LAFS node child process.

    :ivar IProcessTransport magic_folder: The magic-folder child process.
    """
    reactor = attr.ib()
    request = attr.ib()
    temp_dir = attr.ib()
    name = attr.ib()

    tahoe = attr.ib()
    magic_folder = attr.ib()

    magic_folder_web_port = attr.ib()

    @property
    def node_directory(self):
        return join(self.temp_dir, self.name)

    @property
    def magic_directory(self):
        return join(self.temp_dir, "magic-{}".format(self.name))

    @classmethod
    @inlineCallbacks
    def create(
            cls,
            reactor,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            name,
            tahoe_web_port,
            magic_folder_web_port,
            storage,
    ):
        """
        Launch the two processes and return a new ``MagicFolderEnabledNode``
        referencing them.

        Note this depends on pytest/Twisted integration for magical blocking.

        :param reactor: The reactor to use to launch the processes.
        :param request: The pytest request object to use for cleanup.
        :param bytes temp_dir: A directory beneath which to place the
            Tahoe-LAFS node.
        :param bytes introducer_furl: The introducer fURL to configure the new
            Tahoe-LAFS node with.
        :param bytes flog_gatherer: The flog gatherer fURL to configure the
            new Tahoe-LAFS node with.
        :param bytes name: A nickname to assign the new Tahoe-LAFS node.
        :param bytes tahoe_web_port: An endpoint description of the web port
            for the new Tahoe-LAFS node to listen on.
        :param bytes magic_folder_web_port: An endpoint description of the web
            port for the new magic-folder process to listen on.
        :param bool storage: True if the node should offer storage, False
            otherwise.
        """
        # Make the Tahoe-LAFS node process
        tahoe = yield _create_node(
            reactor,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            name,
            tahoe_web_port,
            storage,
            needed=1,
            happy=1,
            total=1,
        )
        await_client_ready(tahoe)

        # Make the magic folder process.
        magic_folder = yield _run_magic_folder(
            reactor,
            request,
            temp_dir,
            name,
            magic_folder_web_port,
        )
        returnValue(
            cls(
                reactor,
                request,
                temp_dir,
                name,
                tahoe,
                magic_folder,
                magic_folder_web_port,
            )
        )

    @inlineCallbacks
    def stop_magic_folder(self):
        self.magic_folder.signalProcess('TERM')
        try:
            yield self.magic_folder.proto.exited
        except ProcessExitedAlready:
            pass

    @inlineCallbacks
    def restart_magic_folder(self):
        yield self.stop_magic_folder()
        yield self.start_magic_folder()

    @inlineCallbacks
    def start_magic_folder(self):
        with start_action(action_type=u"integration:alice:magic_folder:magic-text"):
            self.magic_folder = yield _run_magic_folder(
                self.reactor,
                self.request,
                self.temp_dir,
                self.name,
                self.magic_folder_web_port,
            )


def _run_magic_folder(reactor, request, temp_dir, name, web_port):
    """
    Start a magic-folder process.

    :param reactor: The reactor to use to launch the process.
    :param request: The pytest request object to use for cleanup.
    :param temp_dir: The directory in which to find a Tahoe-LAFS node.
    :param name: The alias of the Tahoe-LAFS node.

    :return Deferred[IProcessTransport]: The started process.
    """
    node_dir = join(temp_dir, name)

    magic_text = "Completed initial Magic Folder setup"
    proto = _MagicTextProtocol(magic_text)

    coverage = request.config.getoption('coverage')
    def optional(flag, elements):
        if flag:
            return elements
        return []

    args = [
        sys.executable,
        "-m",
    ] + optional(coverage, [
        "coverage",
        "run",
        "-m",
    ]) + [
        "magic_folder",
    ] + optional(coverage, [
        "--coverage",
    ]) + [
        "--node-directory",
        node_dir,
        "run",
        "--web-port",
        web_port,
    ]
    Message.log(
        message_type=u"integration:run-magic-folder",
        coverage=coverage,
        args=args,
    )
    transport = reactor.spawnProcess(
        proto,
        sys.executable,
        args,
    )
    request.addfinalizer(partial(_cleanup_tahoe_process, transport, proto.exited))
    with start_action(action_type=u"integration:run-magic-folder").context():
        ctx = DeferredContext(proto.magic_seen)
        ctx.addCallback(lambda ignored: transport)
        return ctx.addActionFinish()


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
