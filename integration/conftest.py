from __future__ import (
    absolute_import,
    division,
    print_function,
)

import sys
import shutil
from time import sleep
from os import mkdir, listdir, environ
from os.path import join, exists
from tempfile import mkdtemp, mktemp
from functools import partial

from foolscap.furl import (
    decode_furl,
)

from eliot import (
    to_file,
    log_call,
)

from twisted.python.procutils import which
from twisted.internet.error import (
    ProcessTerminated,
)

import pytest
import pytest_twisted

from .util import (
    _CollectOutputProtocol,
    _MagicTextProtocol,
    _DumpOutputProtocol,
    _ProcessExitedProtocol,
    _cleanup_tahoe_process,
    _tahoe_runner,
    TahoeProcess,
    _pair_magic_folder,
    _generate_invite,
    MagicFolderEnabledNode,
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
    parser.addoption(
        "--tahoe-requirements", dest="tahoe_requirements",
        help="A 'requirements.txt' file to install Tahoe with",
        default="requirements/tahoe-integration-1.15.txt",
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


# NB: conceptually, it kind of makes sense to parametrize this fixture
# on "Tahoe version" .. a quick attempt at that lead to all but the
# first version failing; I think something doesn't get cleaned-up
# properly between runs "or something". Instead, we use a command-line
# option to specify tahoe-version and have multiple separate runs
# (which looks better in CI anyway).
@pytest.fixture(
    scope='session',
)
def tahoe_venv(request, reactor, tmp_path_factory):
    """
    A virtualenv for our Tahoe install, letting us install a different
    one from the Tahoe we depend on.
    """
    venv_dir = tmp_path_factory.mktemp("venv")
    #venv_dir = join(temp_dir, "tahoe_venv")
    print("creating venv", venv_dir, sys.executable)
    out_protocol = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        out_protocol,
        sys.executable,
        ("python", "-m", "virtualenv", str(venv_dir)),
        env=environ,
    )
    pytest_twisted.blockon(out_protocol.done)
    tahoe_python = venv_dir.joinpath("bin", "python")

    out_protocol = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        out_protocol,
        str(tahoe_python),
        (str(tahoe_python), "-m", "pip", "install", "-r", request.config.getoption("tahoe_requirements")),
        env=environ,
    )
    pytest_twisted.blockon(out_protocol.done)
    return venv_dir


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:flog_binary", include_args=[])
def flog_binary(tahoe_venv):
    return str(tahoe_venv.joinpath("bin", "flogtool"))


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:flog_gatherer", include_args=[])
def flog_gatherer(reactor, temp_dir, flog_binary, request):
    out_protocol = _CollectOutputProtocol()
    gather_dir = join(temp_dir, 'flog_gather')
    reactor.spawnProcess(
        out_protocol,
        flog_binary,
        (
            u'flogtool', u'create-gatherer',
            u'--location', u'tcp:localhost:3117',
            u'--port', u'3117',
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
def introducer(reactor, tahoe_venv, temp_dir, flog_gatherer, request):
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
        _tahoe_runner(
            done_proto,
            reactor,
            tahoe_venv,
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
    transport = _tahoe_runner(
        protocol,
        reactor,
        tahoe_venv,
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


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice", include_args=[], include_result=False)
def alice(reactor, tahoe_venv, temp_dir, introducer_furl, flog_gatherer, request):
    try:
        mkdir(join(temp_dir, 'magic-alice'))
    except OSError:
        pass

    node = pytest_twisted.blockon(
        MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            name=u"alice",
            tahoe_web_port=u"tcp:9980:interface=localhost",
            magic_folder_web_port=u"tcp:19980:interface=localhost",
            storage=True,
        )
    )
    return node


@pytest.fixture(scope='session')
@log_call(action_type=u"integration:bob", include_args=[], include_result=False)
def bob(reactor, tahoe_venv, temp_dir, introducer_furl, flog_gatherer, request):
    try:
        mkdir(join(temp_dir, 'magic-bob'))
    except OSError:
        pass

    return pytest_twisted.blockon(
        MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            name=u"bob",
            tahoe_web_port=u"tcp:9981:interface=localhost",
            magic_folder_web_port=u"tcp:19981:interface=localhost",
            storage=False,
        )
    )

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:edmond", include_args=[], include_result=False)
def edmond(reactor, tahoe_venv, temp_dir, introducer_furl, flog_gatherer, request):
    return pytest_twisted.blockon(
        MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            temp_dir,
            introducer_furl,
            flog_gatherer,
            u"edmond",
            tahoe_web_port=u"tcp:9985:interface=localhost",
            magic_folder_web_port=u"tcp:19985:interface=localhost",
            storage=True,
        )
    )

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice:invite", include_args=["temp_dir"])
def alice_invite(reactor, alice, temp_dir):
    invite = pytest_twisted.blockon(
        _generate_invite(reactor, alice, "bob")
    )
    return invite


@pytest.fixture(scope='session')
@log_call(
    action_type=u"integration:magic_folder",
    include_args=["alice_invite"],
)
def magic_folder(reactor, alice_invite, alice, bob):
    print("pairing magic-folder")
    return pytest_twisted.blockon(
        _pair_magic_folder(reactor, alice_invite, alice, bob)
    )
