from __future__ import print_function

import sys
import shutil
from time import sleep
from os import mkdir, listdir, environ
from os.path import join, exists
from tempfile import mkdtemp, mktemp
from functools import partial

from io import BytesIO

import attr

from foolscap.furl import (
    decode_furl,
)

from eliot import (
    to_file,
    log_call,
    start_action,
)
from eliot.twisted import (
    DeferredContext,
)

from twisted.python.procutils import which
from twisted.internet.defer import DeferredList
from twisted.internet.error import (
    ProcessExitedAlready,
    ProcessTerminated,
)

from magic_folder.scripts.magic_folder_cli import (
    MagicFolderCommand,
    do_magic_folder,
)

import pytest
import pytest_twisted

from util import (
    _CollectOutputProtocol,
    _MagicTextProtocol,
    _DumpOutputProtocol,
    _ProcessExitedProtocol,
    _create_node,
    _run_node,
    _cleanup_tahoe_process,
    _tahoe_runner_optional_coverage,
    await_client_ready,
    TahoeProcess,
)


# pytest customization hooks

def pytest_addoption(parser):
    parser.addoption(
        "--keep-tempdir", action="store_true", dest="keep",
        help="Keep the tmpdir with the client directories (introducer, etc)",
    )
    parser.addoption(
        "--coverage", action="store_true", dest="coverage",
        help="Collect coverage statistics",
    )

@pytest.fixture(autouse=True, scope='session')
def eliot_logging():
    with open("integration.eliot.json", "w") as f:
        to_file(f)
        yield


# I've mostly defined these fixtures from "easiest" to "most
# complicated", and the dependencies basically go "down the
# page". They're all session-scoped which has the "pro" that we only
# set up the grid once, but the "con" that each test has to be a
# little careful they're not stepping on toes etc :/

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:reactor", include_result=False)
def reactor():
    # this is a fixture in case we might want to try different
    # reactors for some reason.
    from twisted.internet import reactor as _reactor
    return _reactor


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:temp_dir", include_args=[])
def temp_dir(request):
    """
    Invoke like 'py.test --keep-tempdir ...' to avoid deleting the temp-dir
    """
    tmp = mkdtemp(prefix="tahoe")
    if request.config.getoption('keep'):
        print("\nWill retain tempdir '{}'".format(tmp))

    # I'm leaving this in and always calling it so that the tempdir
    # path is (also) printed out near the end of the run
    def cleanup():
        if request.config.getoption('keep'):
            print("Keeping tempdir '{}'".format(tmp))
        else:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception as e:
                print("Failed to remove tmpdir: {}".format(e))
    request.addfinalizer(cleanup)

    return tmp


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:flog_binary", include_args=[])
def flog_binary():
    return which('flogtool')[0]


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:flog_gatherer", include_args=[])
def flog_gatherer(reactor, temp_dir, flog_binary, request):
    out_protocol = _CollectOutputProtocol()
    gather_dir = join(temp_dir, 'flog_gather')
    reactor.spawnProcess(
        out_protocol,
        flog_binary,
        (
            'flogtool', 'create-gatherer',
            '--location', 'tcp:localhost:3117',
            '--port', '3117',
            gather_dir,
        )
    )
    pytest_twisted.blockon(out_protocol.done)

    twistd_protocol = _MagicTextProtocol("Gatherer waiting at")
    twistd_process = reactor.spawnProcess(
        twistd_protocol,
        which('twistd')[0],
        (
            'twistd', '--nodaemon', '--python',
            join(gather_dir, 'gatherer.tac'),
        ),
        path=gather_dir,
    )
    pytest_twisted.blockon(twistd_protocol.magic_seen)

    def cleanup():
        _cleanup_tahoe_process(twistd_process, twistd_protocol.exited)

        flog_file = mktemp('.flog_dump')
        flog_protocol = _DumpOutputProtocol(open(flog_file, 'w'))
        flog_dir = join(temp_dir, 'flog_gather')
        flogs = [x for x in listdir(flog_dir) if x.endswith('.flog')]

        print("Dumping {} flogtool logfiles to '{}'".format(len(flogs), flog_file))
        reactor.spawnProcess(
            flog_protocol,
            flog_binary,
            (
                'flogtool', 'dump', join(temp_dir, 'flog_gather', flogs[0])
            ),
        )
        print("Waiting for flogtool to complete")
        try:
            pytest_twisted.blockon(flog_protocol.done)
        except ProcessTerminated as e:
            print("flogtool exited unexpectedly: {}".format(str(e)))
        print("Flogtool completed")

    request.addfinalizer(cleanup)

    with open(join(gather_dir, 'log_gatherer.furl'), 'r') as f:
        furl = f.read().strip()
    return furl


@pytest.fixture(scope='session')
@log_call(
    action_type=u"integration:introducer",
    include_args=["temp_dir", "flog_gatherer"],
    include_result=False,
)
def introducer(reactor, temp_dir, flog_gatherer, request):
    config = '''
[node]
nickname = introducer0
web.port = 4560
log_gatherer.furl = {log_furl}
tub.port = tcp:9321
tub.location = tcp:localhost:9321
'''.format(log_furl=flog_gatherer)

    intro_dir = join(temp_dir, 'introducer')
    print("making introducer", intro_dir)

    if not exists(intro_dir):
        mkdir(intro_dir)
        done_proto = _ProcessExitedProtocol()
        _tahoe_runner_optional_coverage(
            done_proto,
            reactor,
            request,
            (
                'create-introducer',
                '--listen=tcp',
                '--hostname=localhost',
                intro_dir,
            ),
        )
        pytest_twisted.blockon(done_proto.done)

    # over-write the config file with our stuff
    with open(join(intro_dir, 'tahoe.cfg'), 'w') as f:
        f.write(config)

    # on windows, "tahoe start" means: run forever in the foreground,
    # but on linux it means daemonize. "tahoe run" is consistent
    # between platforms.
    protocol = _MagicTextProtocol('introducer running')
    transport = _tahoe_runner_optional_coverage(
        protocol,
        reactor,
        request,
        (
            'run',
            intro_dir,
        ),
    )
    request.addfinalizer(partial(_cleanup_tahoe_process, transport, protocol.exited))

    pytest_twisted.blockon(protocol.magic_seen)
    return TahoeProcess(transport, intro_dir)


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:introducer:furl", include_args=["temp_dir"])
def introducer_furl(introducer, temp_dir):
    furl_fname = join(temp_dir, 'introducer', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        sleep(.1)
    furl = open(furl_fname, 'r').read()
    # Make sure it is valid.
    _, location, _ = decode_furl(furl)
    if location == []:
        raise Exception("introducer furl with no location hints: {}".format(furl))
    return furl


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

    @classmethod
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
        tahoe = pytest_twisted.blockon(
            _create_node(
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
        )
        await_client_ready(tahoe)

        # Make the magic folder process.
        magic_folder = pytest_twisted.blockon(
            _run_magic_folder(
                reactor,
                request,
                temp_dir,
                name,
                magic_folder_web_port,
            ),
        )
        return cls(
            reactor,
            request,
            temp_dir,
            name,
            tahoe,
            magic_folder,
            magic_folder_web_port,
        )

    @pytest_twisted.inlineCallbacks
    def stop_magic_folder(self):
        self.magic_folder.signalProcess('TERM')
        try:
            yield self.magic_folder.proto.exited
        except ProcessExitedAlready:
            pass

    @pytest_twisted.inlineCallbacks
    def restart_magic_folder(self):
        yield self.stop_magic_folder()
        yield self.start_magic_folder()

    @pytest_twisted.inlineCallbacks
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

    args = [
        sys.executable,
        "-m",
        "magic_folder",
        "--node-directory",
        node_dir,
        "run",
        "--web-port", web_port,
    ]
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

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice", include_args=[], include_result=False)
def alice(reactor, temp_dir, introducer_furl, flog_gatherer, request):
    try:
        mkdir(join(temp_dir, 'magic-alice'))
    except OSError:
        pass

    return MagicFolderEnabledNode.create(
        reactor,
        request,
        temp_dir,
        introducer_furl,
        flog_gatherer,
        name="alice",
        tahoe_web_port="tcp:9980:interface=localhost",
        magic_folder_web_port="tcp:19980:interface=localhost",
        storage=True,
    )


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:bob", include_args=[], include_result=False)
def bob(reactor, temp_dir, introducer_furl, flog_gatherer, request):
    try:
        mkdir(join(temp_dir, 'magic-bob'))
    except OSError:
        pass

    return MagicFolderEnabledNode.create(
        reactor,
        request,
        temp_dir,
        introducer_furl,
        flog_gatherer,
        name="bob",
        tahoe_web_port="tcp:9981:interface=localhost",
        magic_folder_web_port="tcp:19981:interface=localhost",
        storage=False,
    )

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:edmond", include_args=[], include_result=False)
def edmond(reactor, temp_dir, introducer_furl, flog_gatherer, request):
    return MagicFolderEnabledNode.create(
        reactor,
        request,
        temp_dir,
        introducer_furl,
        flog_gatherer,
        "edmond",
        tahoe_web_port="tcp:9985:interface=localhost",
        magic_folder_web_port="tcp:19985:interface=localhost",
        storage=True,
    )

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice:invite", include_args=["temp_dir"])
def alice_invite(reactor, alice, temp_dir, request):
    node_dir = join(temp_dir, 'alice')

    with start_action(action_type=u"integration:alice:magic_folder:create"):
        print("Creating magic-folder for {}".format(node_dir))
        o = MagicFolderCommand()
        o.parseOptions([
            "--node-directory", node_dir,
            "create",
            "--poll-interval", "2", "magik:", "alice", join(temp_dir, "magic-alice"),
        ])
        assert 0 == do_magic_folder(o)


    with start_action(action_type=u"integration:alice:magic_folder:invite") as a:
        print("Inviting bob to magic-folder for {}".format(node_dir))
        o = MagicFolderCommand()
        o.stdout = BytesIO()
        o.parseOptions([
            "--node-directory", node_dir,
            "invite",
            "magik:", "bob",
        ])
        assert 0 == do_magic_folder(o)
        invite = o.stdout.getvalue()
        a.add_success_fields(invite=invite)

    with start_action(action_type=u"integration:alice:magic_folder:restart"):
        # before magic-folder works, we have to stop and restart (this is
        # crappy for the tests -- can we fix it in magic-folder?)
        pytest_twisted.blockon(alice.restart_magic_folder())
    return invite


@pytest.fixture(scope='session')
@log_call(
    action_type=u"integration:magic_folder",
    include_args=["alice_invite", "temp_dir"],
)
def magic_folder(reactor, alice_invite, alice, bob, temp_dir, request):
    print("pairing magic-folder")
    bob_dir = join(temp_dir, 'bob')

    print("Joining bob to magic-folder")
    o = MagicFolderCommand()
    o.parseOptions([
        "--node-directory", bob_dir,
        "join",
        "--poll-interval", "1",
        alice_invite,
        join(temp_dir, "magic-bob"),
    ])
    assert 0 == do_magic_folder(o)

    # before magic-folder works, we have to stop and restart (this is
    # crappy for the tests -- can we fix it in magic-folder?)
    pytest_twisted.blockon(bob.restart_magic_folder())

    return (join(temp_dir, 'magic-alice'), join(temp_dir, 'magic-bob'))
