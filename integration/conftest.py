import subprocess
import sys
from configparser import ConfigParser
from os import environ, listdir, mkdir
from os.path import exists, join

import attr
from twisted.python.filepath import FilePath
import pytest
import pytest_twisted
from eliot import log_call, start_task, to_file
from foolscap.furl import decode_furl
from twisted.internet.error import ProcessTerminated

from .util import (
    MagicFolderEnabledNode,
    _CollectOutputProtocol,
    _DumpOutputProtocol,
    _generate_invite,
    _pair_magic_folder,
    _ProcessExitedProtocol,
    _tahoe_runner,
    run_service,
    run_tahoe_service,
    sleep,
)


# pytest customization hooks

def pytest_addoption(parser):
    parser.addoption(
        "--coverage", action="store_true", dest="coverage",
        help="Collect coverage statistics",
    )
    parser.addoption(
        "--tahoe-tox-env", dest="tahoe_tox_env",
        help="A tox env to run tahoe from.",
        default="tahoe1_17",
    )
    parser.addoption(
        "--gather-foolscap-logs", action="store_true", dest="gather_foolscap_logs",
        help="Gather foolscap logs from tahoe processes.",
        default=False,
    )

@pytest.fixture(autouse=True, scope='session')
def eliot_logging():
    with open("eliot.log", "w") as f:
        to_file(f)
        yield


@pytest.fixture(autouse=True, scope='function')
def eliot_log_test(request):
    with start_task(action_type="integration:pytest", test=str(request.node.nodeid)):
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
@log_call(action_type=u"integration:base_dir", include_args=[])
def base_dir(tmp_path_factory):
    """
    Base temporary directory, used by several session scoped fixtures.

    :param _pytest.tmpdir.TempPathFactory tmp_path_factory:
        A session-scoped pytest fixture that allows creating temporary
        directories.
    """
    base = tmp_path_factory.mktemp("magic-folder")
    return str(base)


@pytest.fixture(scope='function')
def temp_filepath(tmp_path):
    """
    Fixture that returns a per test function temporary directory represented
    as a :py:`FilePath`.

    :param pathlib.Path tmp_path: A function-scoped pytest fixture that provides
        a temporary directory.
    """
    return FilePath(str(tmp_path))


@attr.s
class VirtualEnv(object):
    """
    A python virtual environemnt.

    :ivar Path base_dir: The base directory of the virtualenv.
    """

    base_dir = attr.ib(validator=attr.validators.instance_of(FilePath))

    @property
    def bin_dir(self):
        """
        The directory containing executables of the virtual environment.
        """
        if sys.platform == 'win32':
            return self.base_dir.child("Scripts")
        else:
            return self.base_dir.child("bin")

    def bin(self, executable):
        """
        Returns the path to the named executable from the virtual environment.
        """
        return self.bin_dir.child(executable)

    @property
    def python(self):
        """
        The python executable of the virtualenv.
        """
        if sys.platform == 'win32':
            return self.bin("python.exe").path
        else:
            return self.bin("python").path


# NB: conceptually, it kind of makes sense to parametrize this fixture
# on "Tahoe version" .. a quick attempt at that lead to all but the
# first version failing; I think something doesn't get cleaned-up
# properly between runs "or something". Instead, we use a command-line
# option to specify tahoe-version and have multiple separate runs
# (which looks better in CI anyway).
@pytest.fixture(
    scope='session',
)
def tahoe_venv(request, reactor):
    """
    A virtualenv for our Tahoe install, letting us install a different
    one from the Tahoe we depend on.
    """
    tahoe_env = request.config.getoption("tahoe_tox_env")
    print("creating venv")
    out_protocol = _DumpOutputProtocol(None)
    reactor.spawnProcess(
        out_protocol,
        sys.executable,
        ("python", "-m", "tox", "--notest", "-e", tahoe_env),
        env=environ,
    )
    pytest_twisted.blockon(out_protocol.done)

    output = subprocess.check_output(
        [sys.executable, "-m", "tox", "-e", request.config.getoption("tahoe_tox_env"), "--showconfig"],
        env=environ,
    )

    parser = ConfigParser(strict=False)
    parser.read_string(output.decode("utf-8"))

    venv_dir = parser.get("testenv:{}".format(tahoe_env), 'envdir')

    return VirtualEnv(FilePath(venv_dir))


@pytest.fixture(scope='session')
def flog_binary(tahoe_venv):
    return str(tahoe_venv.bin("flogtool"))


@pytest.fixture(scope='session')
def flog_gatherer(reactor, base_dir, tahoe_venv, flog_binary, request):
    if not request.config.getoption("gather_foolscap_logs"):
        return ""

    with start_task(action_type=u"integration:flog_gatherer"):
        out_protocol = _CollectOutputProtocol()
        gather_dir = join(base_dir, 'flog_gather')
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

        magic_text = "Gatherer waiting at"
        executable = str(tahoe_venv.bin('twistd'))
        args = (
            'twistd', '--nodaemon', '--python',
            join(gather_dir, 'gatherer.tac'),
        )
        action_fields = {
            "action_type": u"integration:flog-gatherer:service",
        }
        pytest_twisted.blockon(
            run_service(reactor, request, action_fields, magic_text, executable, args, cwd=gather_dir)
        )

        def cleanup():
            flog_file = 'integration.flog_dump'
            flog_protocol = _DumpOutputProtocol(open(flog_file, 'w'))
            flogs = [x for x in listdir(gather_dir) if x.endswith('.flog')]

            print("Dumping {} flogtool logfiles to '{}'".format(len(flogs), flog_file))
            reactor.spawnProcess(
                flog_protocol,
                flog_binary,
                (
                    'flogtool', 'dump', join(gather_dir, flogs[0])
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
def introducer(reactor, tahoe_venv, base_dir, flog_gatherer, request):
    with start_task(action_type=u"integration:introducer").context():
        config = '''
[node]
nickname = introducer0
web.port = 4560
log_gatherer.furl = {log_furl}
tub.port = tcp:9321
tub.location = tcp:localhost:9321
'''.format(log_furl=flog_gatherer)

        intro_dir = join(base_dir, 'introducer')
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

        magic_text = 'introducer running'
        action_fields = {
            "action_type": u"integration:introducer:service",
        }
        return pytest_twisted.blockon(
            run_tahoe_service(reactor, request, action_fields, magic_text, tahoe_venv, intro_dir)
        )


@pytest.fixture(scope='session')
def introducer_furl(introducer, base_dir):
    furl_fname = join(base_dir, 'introducer', 'private', 'introducer.furl')
    while not exists(furl_fname):
        print("Don't see {} yet".format(furl_fname))
        sleep(.1)
    furl = open(furl_fname, 'r').read()
    # Make sure it is valid.
    _, location, _ = decode_furl(furl)
    if location == []:
        raise Exception("introducer furl with no location hints: {}".format(furl))
    return furl


@pytest.fixture(scope='function', autouse=True)
def magic_folder_nodes(request):
    nodes = {
        name: fixture
        for name, fixture in request.node.funcargs.items()
        if isinstance(fixture, MagicFolderEnabledNode)
    }
    return nodes


@pytest.fixture(scope='session')
def alice(reactor, tahoe_venv, base_dir, introducer_furl, flog_gatherer, request):
    try:
        mkdir(join(base_dir, 'magic-alice'))
    except OSError:
        pass

    node = pytest_twisted.blockon(
        MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            base_dir,
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
def bob(reactor, tahoe_venv, base_dir, introducer_furl, flog_gatherer, request):
    try:
        mkdir(join(base_dir, 'magic-bob'))
    except OSError:
        pass

    return pytest_twisted.blockon(
        MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            base_dir,
            introducer_furl,
            flog_gatherer,
            name=u"bob",
            tahoe_web_port=u"tcp:9981:interface=localhost",
            magic_folder_web_port=u"tcp:19981:interface=localhost",
            storage=False,
        )
    )

@pytest.fixture(scope='session')
def edmond(reactor, tahoe_venv, base_dir, introducer_furl, flog_gatherer, request):
    return pytest_twisted.blockon(
        MagicFolderEnabledNode.create(
            reactor,
            tahoe_venv,
            request,
            base_dir,
            introducer_furl,
            flog_gatherer,
            u"edmond",
            tahoe_web_port=u"tcp:9985:interface=localhost",
            magic_folder_web_port=u"tcp:19985:interface=localhost",
            storage=True,
        )
    )

@pytest.fixture(scope='session')
@log_call(action_type=u"integration:alice:invite", include_args=["base_dir"])
def alice_invite(reactor, alice, base_dir):
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
