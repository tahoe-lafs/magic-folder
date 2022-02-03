import sys
import time
import json
import sqlite3
import os
from os import mkdir
from io import (
    BytesIO,
    StringIO,
)
from os.path import exists, join
from functools import partial

import attr
from psutil import (
    Process,
    STATUS_RUNNING,
)

from treq.client import HTTPClient
from twisted.internet.defer import (
    returnValue,
    Deferred,
    maybeDeferred,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.protocol import (
    ProcessProtocol,
)
from twisted.internet.error import (
    ProcessExitedAlready,
    ProcessDone,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.client import Agent

import treq

from eliot import (
    log_message,
    current_action,
    start_action,
    start_task,
)
from eliot.twisted import (
    inline_callbacks,
)

from allmydata.util.configutil import (
    get_config,
    set_config,
    write_config,
)
from allmydata import client

import pytest_twisted

from magic_folder.cli import (
    MagicFolderCommand,
    run_magic_folder_options,
)
from magic_folder.config import (
    load_global_configuration,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)
from magic_folder.client import (
    create_http_client,
    create_magic_folder_client,
)
from magic_folder.util.eliotutil import (
    log_inline_callbacks,
)


@attr.s
class MagicFolderEnabledNode(object):
    """
    Keep track of a Tahoe-LAFS node child process and an associated
    magic-folder child process.

    :ivar IProcessTransport tahoe: The Tahoe-LAFS node child process.
    :ivar IProcessTransport magic_folder: The magic-folder child process.

    :ivar eliot.Action action: This is the top-level action for this node.
       It is used for capturing all the in-test-process logs for the services
       related to the node. In particular, when restarting a servie during a
       test, we want to capture logs from that processes output in *this*
       action, rather than the test action, since the process likely continues
       until after the test ends.
    """
    reactor = attr.ib()
    request = attr.ib()
    base_dir = attr.ib()
    name = attr.ib()

    action = attr.ib()

    tahoe = attr.ib()
    magic_folder = attr.ib()

    magic_folder_web_port = attr.ib()

    _global_config = attr.ib(init=False, default=None)
    _client = attr.ib(default=None)

    @property
    def node_directory(self):
        return join(self.base_dir, self.name)

    @property
    def magic_config_directory(self):
        return join(self.base_dir, "magic-daemon-{}".format(self.name))

    def global_config(self):
        if self._global_config is None:
            self._global_config = load_global_configuration(FilePath(self.magic_config_directory))
        return self._global_config

    def tahoe_client(self):
        config = self.global_config()
        return create_tahoe_client(
            config.tahoe_client_url,
            HTTPClient(Agent(self.reactor)),
        )

    @property
    def client(self):
        if self._client is None:
            self._client = create_magic_folder_client(
                self.reactor,
                self.global_config(),
                create_http_client(
                    self.reactor,
                    self.global_config().api_client_endpoint,
                ),
            )
        return self._client

    @property
    def magic_directory(self):
        return join(self.base_dir, "magic-{}".format(self.name))

    @classmethod
    @inline_callbacks
    def create(
            cls,
            reactor,
            tahoe_venv,
            request,
            base_dir,
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
        :param tahoe_venv: Directory where our virtualenv is located.
        :param request: The pytest request object to use for cleanup.
        :param bytes base_dir: A directory beneath which to place the
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
        with start_task(action_type=u"integration:magic-folder-node", node=name).context() as action:
            # We want to last until the session fixture using it ends (so we
            # can capture output from every process associated to this node).
            # Thus we use `.context()` above so this with-block doesn't finish
            # the action, and add a finalizer to finish it (first, since
            # finalizers are a stack).
            request.addfinalizer(action.finish)
            # Make the Tahoe-LAFS node process
            tahoe = yield _create_node(
                reactor,
                tahoe_venv,
                request,
                base_dir,
                introducer_furl,
                flog_gatherer,
                name,
                tahoe_web_port,
                storage,
                needed=1,
                happy=1,
                total=1,
            )
            yield await_client_ready(reactor, tahoe)

            # Create the magic-folder daemon config
            yield _init_magic_folder(
                reactor,
                request,
                base_dir,
                name,
                magic_folder_web_port,
            )

            # Run the magic-folder daemon
            magic_folder = yield _run_magic_folder(
                reactor,
                request,
                base_dir,
                name,
            )

        returnValue(
            cls(
                reactor,
                request,
                base_dir,
                name,
                action,
                tahoe,
                magic_folder,
                magic_folder_web_port,
            )
        )

    @inline_callbacks
    def stop_magic_folder(self):
        log_message(message_type=u"integation:magic-folder:stop", node=self.name)
        if self.magic_folder is None:
            return
        try:
            log_message(
                message_type=u"integation:magic-folder:stop",
                node=self.name,
                signal="TERM",
            )
            self.magic_folder.signalProcess('TERM')
            yield self.magic_folder.proto.exited
            self.magic_folder = None
        except ProcessExitedAlready:
            pass

    @inline_callbacks
    def restart_magic_folder(self):
        yield self.stop_magic_folder()
        yield self.start_magic_folder()

    @inline_callbacks
    def start_magic_folder(self):
        if self.magic_folder is not None:
            return
        # We log a notice that we are starting the service in the context of the test
        # but the logs of the service are in the context of the fixture.
        log_message(message_type=u"integation:magic-folder:start", node=self.name)
        with self.action.context():
            self.magic_folder = yield _run_magic_folder(
                self.reactor,
                self.request,
                self.base_dir,
                self.name,
            )

    def pause_tahoe(self):
        log_message(message_type=u"integation:tahoe-node:pause", node=self.name)
        print("suspend tahoe: {}".format(self.name))
        self.tahoe.suspend()

    def resume_tahoe(self):
        log_message(message_type=u"integation:tahoe-node:resume", node=self.name)
        print("resume tahoe: {}".format(self.name))
        self.tahoe.resume()

    # magic-folder CLI API helpers

    def add(self, folder_name, magic_directory, author=None, poll_interval=5, scan_interval=None):
        """
        magic-folder add
        """
        args = [
            "--config",
            self.magic_config_directory,
            "add",
            "--name",
            folder_name,
            "--author",
            author or self.name,
            "--poll-interval",
            str(poll_interval),
        ]
        if scan_interval is None:
            args += ["--disable-scanning"]
        else:
            args += [
                "--scan-interval",
                str(scan_interval),
            ]
        args += [
            magic_directory,
        ]
        return _magic_folder_runner(
            self.reactor,
            self.request,
            self.name,
            args,
        )

    def leave(self, folder_name):
        """
        magic-folder leave
        """
        if self._global_config is not None:
            # If we've accessed the folder state database from the integration
            # tests, make sure that the connection has been closed before we
            # try to remove the database. This is necessary on windows,
            # otherwise the state database can't be removed.
            folder_config = self._global_config._folder_config_cache.pop(
                folder_name, None
            )
            if folder_config is not None:
                folder_config._database.close()

        return _magic_folder_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "leave",
                "--name", folder_name,
                "--really-delete-write-capability",
            ],
        )

    def show_config(self):
        """
        magic-folder show-config
        """
        return _magic_folder_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "show-config",
            ],
        ).addCallback(json.loads)

    def list_(self, include_secret_information=None):
        """
        magic-folder list
        """
        args = [
            "--config", self.magic_config_directory,
            "list",
            "--json",
        ]
        if include_secret_information:
            args.append("--include-secret-information")

        return _magic_folder_runner(
            self.reactor, self.request, self.name,
            args,
        ).addCallback(json.loads)

    def add_snapshot(self, folder_name, relpath):
        """
        magic-folder-api add-snapshot
        """
        return _magic_folder_api_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "add-snapshot",
                "--folder", folder_name,
                "--file", relpath,
            ],
        )

    def scan_folder(self, folder_name):
        """
        magic-folder-api scan-folder
        """
        return _magic_folder_api_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "scan",
                "--folder", folder_name,
            ],
        )

    def add_participant(self, folder_name, author_name, personal_dmd):
        """
        magic-folder-api add-participant
        """
        return _magic_folder_api_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "add-participant",
                "--folder", folder_name,
                "--author", author_name,
                "--personal-dmd", personal_dmd,
            ],
        )

    def scan(self, folder_name):
        """
        magic-folder-api scan
        """
        return _magic_folder_api_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "scan",
                "--folder", folder_name,
            ],
        )

    def status(self):
        """
        magic-folder-api monitor --once
        """
        return _magic_folder_api_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "monitor",
                "--once",
            ],
        )

    def dump_state(self, folder_name):
        """
        magic-folder-api dump-state
        """
        return _magic_folder_api_runner(
            self.reactor, self.request, self.name,
            [
                "--config", self.magic_config_directory,
                "dump-state",
                "--folder", folder_name,
            ],
        )


class _ProcessExitedProtocol(ProcessProtocol):
    """
    Internal helper that .callback()s on self.done when the process
    exits (for any reason).
    """

    def __init__(self):
        self.done = Deferred()

    def processEnded(self, reason):
        self.done.callback(None)


class _CollectOutputProtocol(ProcessProtocol):
    """
    Internal helper. Collects all output (stdout + stderr) into
    self.output, and callback's on done with all of it after the
    process exits (for any reason).
    """
    def __init__(self):
        self.done = Deferred()
        self.output = StringIO()
        self._action = current_action()
        assert self._action is not None

    def processEnded(self, reason):
        if not self.done.called:
            self.done.callback(self.output.getvalue())

    def processExited(self, reason):
        if not isinstance(reason.value, ProcessDone):
            self.done.errback(reason)

    def outReceived(self, data):
        self.output.write(data.decode(sys.getfilesystemencoding()))

    def errReceived(self, data):
        print("ERR: {}".format(data.decode(sys.getfilesystemencoding())))
        with self._action.context():
            log_message(message_type=u"err-received", data=data.decode(sys.getfilesystemencoding()))
        self.output.write(data.decode(sys.getfilesystemencoding()))


class _DumpOutputProtocol(ProcessProtocol):
    """
    Internal helper.
    """
    def __init__(self, f):
        self.done = Deferred()
        self._out = f if f is not None else sys.stdout

    def processEnded(self, reason):
        if not self.done.called:
            self.done.callback(None)

    def processExited(self, reason):
        if not isinstance(reason.value, ProcessDone):
            self.done.errback(reason)

    def outReceived(self, data):
        self._out.write(str(data, "utf8"))

    def errReceived(self, data):
        self._out.write(str(data, "utf8"))


@attr.s
class EliotLogStream(object):
    """
    Capture a stream of eliot logs and feed it to the eliot logger.

    This is intended to capture eliot log output from a subprocess, and include
    them in the logs for this process.

    :ivar Callable[[str], None] _fallback: A function to call with non-JSON log lines.
    """
    _fallback = attr.ib()
    _eliot_buffer = attr.ib(init=False, default=b"")

    def data_received(self, data):
        # We write directly to the logger, as we don't want
        # eliot.Message to add its default fields.
        from eliot._output import _DEFAULT_LOGGER as logger

        lines = (self._eliot_buffer + data).split(b'\n')
        self._eliot_buffer = lines.pop(-1)
        for line in lines:
            try:
                message = json.loads(line)
            except ValueError:
                self._fallback(line)
            else:
                logger.write(message)


def run_service(
    reactor,
    request,
    action_fields,
    magic_text,
    executable,
    args,
    cwd=None
):
    """
    Start a service, and capture the output from the service in an eliot
    action.

    This will start the service, and the returned deferred will fire with
    the process, once the given magic text is seeen.

    This will capture eliot logs from file descriptor 3 of the process.

    :param reactor: The reactor to use to launch the process.
    :param request: The pytest request object to use for cleanup.
    :param dict action_fields: Additional fields to include in the action.
    :param magic_text: Text to look for in the logs, that indicate the service
        is ready to accept requests.
    :param executable: The executable to run.
    :param args: The arguments to pass to the process.
    :param cwd: The working directory of the process.

    :return Deferred[IProcessTransport]: The started process.
    """
    with start_action(args=args, executable=executable, **action_fields).context() as ctx:
        protocol = _MagicTextProtocol(magic_text)

        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        process = reactor.spawnProcess(
            protocol,
            executable,
            args,
            path=cwd,
            # Twisted on Windows doesn't support customizing FDs
            # _MagicTextProtocol will collect eliot logs from FD 3 and stderr.
            childFDs={1: 'r', 2: 'r', 3: 'r'} if sys.platform != "win32" else None,
            env=env,
        )
        request.addfinalizer(partial(_cleanup_service_process, process, protocol.exited, ctx))
        return protocol.magic_seen.addCallback(lambda ignored: process)

def run_tahoe_service(
    reactor,
    request,
    action_fields,
    magic_text,
    tahoe_venv,
    node_dir,
    cwd=None,
):
    """
    Start a tahoe node, and capture the output from the service in an eliot
    action.

    This will start the service, and the returned deferred will fire with
    the process, once the given magic text is seeen.

    :param reactor: The reactor to use to launch the process.
    :param request: The pytest request object to use for cleanup.
    :param dict action_fields: Additional fields to include in the action.
    :param magic_text: Text to look for in the logs, that indicate the service
        is ready to accept requests.
    :param tahoe_venv: The path to the tahoe virtualenv to use.
    :param node_dir: The node directory
    :param cwd: The working directory of the process.

    :return Deferred[TahoeProcess]: The started process.
    """
    # on windows, "tahoe start" means: run forever in the foreground,
    # but on linux it means daemonize. "tahoe run" is consistent
    # between platforms.
    executable, args = _tahoe_runner_args(tahoe_venv, [
        '--eliot-destination', 'file:{}/logs/eliot.json'.format(node_dir),
        'run',
        node_dir,
    ])
    d = run_service(reactor, request, action_fields, magic_text, executable, args, cwd=cwd)
    return d.addCallback(TahoeProcess, node_dir=node_dir)


class _MagicTextProtocol(ProcessProtocol):
    """
    Internal helper. Monitors all stdout looking for a magic string,
    and then .callback()s on self.done and .errback's if the process exits

    Also capture eliot logs from file descriptor 3, and logs them.
    """

    def __init__(self, magic_text):
        self.magic_seen = Deferred()
        self.exited = Deferred()
        self._magic_text = magic_text
        self._output = StringIO()
        self._eliot_stream = EliotLogStream(fallback=self.eliot_garbage_received)
        self._eliot_stderr = EliotLogStream(fallback=self.err_received)
        self._action = current_action()
        assert self._action is not None

    def processEnded(self, reason):
        with self._action:
            log_message(message_type=u"process-ended")
        if self.magic_seen is not None:
            d, self.magic_seen = self.magic_seen, None
            d.errback(Exception("Service failed."))
        self.exited.callback(None)

    def childDataReceived(self, childFD, data):
        if childFD == 1:
            self.out_received(data)
        elif childFD == 2:
            self._eliot_stderr.data_received(data)
        elif childFD == 3:
            self._eliot_stream.data_received(data)
        else:
            ProcessProtocol.childDataReceived(self, childFD, data)

    def out_received(self, data):
        """
        Called with output from stdout.
        """
        with self._action.context():
            log_message(message_type=u"out-received", data=data.decode("utf8"))
            sys.stdout.write(data.decode("utf8"))
            self._output.write(data.decode("utf8"))
        if self.magic_seen is not None and self._magic_text in self._output.getvalue():
            print("Saw '{}' in the logs".format(self._magic_text))
            d, self.magic_seen = self.magic_seen, None
            d.callback(self)

    def err_received(self, data):
        """
        Called when non-JSON lines are received on stderr.

        On Windows we use stderr for eliot logs from magic-folder.
        But neither magic-folder nor tahoe guarantee that there is
        no other output there, so we treat it as expected.
        """
        with self._action.context():
            log_message(message_type=u"err-received", data=data.decode("utf8"))
            sys.stdout.write(data.decode("utf8"))

    def eliot_garbage_received(self, data):
        """
        Called when non-JSON lines are received on FD 3.

        Since FD 3 is suppposed to only have eliot-logs, log them as malformed.
        """
        with self._action.context():
            log_message(message_type=u"malformed-eliot-log", data=data.decode("utf8"))


def _cleanup_service_process(process, exited, action):
    """
    Terminate the given process with a kill signal (SIGKILL on POSIX,
    TerminateProcess on Windows).

    :param process: The `IProcessTransport` representing the process.
    :param exited: A `Deferred` which fires when the process has exited.

    :return: After the process has exited.
    """
    try:
        with action.context():
            def report(m):
                log_message(message_type="integration:cleanup", message=m)
                print(m)
            if process.pid is not None:
                report("signaling {} with TERM".format(process.pid))
                process.signalProcess('TERM')
                report("signaled, blocking on exit")
                pytest_twisted.blockon(exited)
            report("exited, goodbye")
    except ProcessExitedAlready:
        pass


@inline_callbacks
def _package_runner(reactor, request, action_fields, package, other_args):
    """
    Launch a python package and return the output.

    Gathers coverage of the command, if requested for pytest.
    """
    with start_action(
        args=other_args,
        **action_fields
    ) as action:
        proto = _CollectOutputProtocol()

        if request.config.getoption('coverage'):
            prelude = [sys.executable, "-m", "coverage", "run", "-m", package]
        else:
            prelude = [sys.executable, "-m", package]

        reactor.spawnProcess(
            proto,
            sys.executable,
            prelude + other_args,
        )
        output = yield proto.done

        action.add_success_fields(output=output)

    returnValue(output)


def _magic_folder_runner(reactor, request, name, other_args):
    """
    Launch a ``magic_folder`` sub-command and return the output.
    """
    action_fields = {
            "action_type": "integration:magic-folder:run-cli",
            "node": name,
    }
    return _package_runner(
        reactor,
        request,
        action_fields,
        "magic_folder",
        other_args,
    )


def _magic_folder_api_runner(reactor, request, name, other_args):
    """
    Launch a ``magic-folder-api`` command and return the output.
    """
    action_fields = {
        "action_type": "integration:magic-folder:run-cli-api",
        "node": name,
    }
    return _package_runner(
        reactor,
        request,
        action_fields,
        "magic_folder.api_cli",
        other_args,
    )


def _tahoe_runner_args(tahoe_venv, other_args):
    tahoe_python = str(tahoe_venv.python)
    args = [tahoe_python, '-m', 'allmydata.scripts.runner']
    args.extend(other_args)
    return tahoe_python, args


def _tahoe_runner(proto, reactor, tahoe_venv, request, other_args):
    """
    Internal helper. Calls spawnProcess with `-m allmydata.scripts.runner` and
    `other_args`.
    """
    executable, args = _tahoe_runner_args(tahoe_venv, other_args)
    return reactor.spawnProcess(
        proto,
        executable,
        args,
    )


class TahoeProcess(object):
    """
    A running Tahoe process, with associated information.
    """

    def __init__(self, process_transport, node_dir):
        self._process_transport = process_transport  # IProcessTransport instance
        self._node_dir = node_dir  # path

    @property
    def transport(self):
        return self._process_transport

    def suspend(self):
        if self.transport.pid is not None:
            p = Process(self.transport.pid)
            p.suspend()
            while p.status() == STATUS_RUNNING:
                print("suspend {}: still running".format(self._node_dir))
                continue
            print("  status: {}".format(p.status()))
        else:
            raise RuntimeError(
                "Cannot suspend Tahoe: no PID available"
            )

    def resume(self):
        if self.transport.pid is not None:
            p = Process(self.transport.pid)
            p.resume()
            while p.status() != STATUS_RUNNING:
                print("resume {}: not running".format(self._node_dir))
        else:
            raise RuntimeError(
                "Cannot resume Tahoe: no PID available"
            )

    @property
    def node_dir(self):
        return self._node_dir

    def get_config(self):
        return client.read_config(
            self._node_dir,
            u"portnum",
        )

    def __str__(self):
        return "<TahoeProcess in '{}'>".format(self._node_dir)


@inline_callbacks
def _create_node(reactor, tahoe_venv, request, base_dir, introducer_furl, flog_gatherer, name, web_port,
                 storage=True,
                 magic_text=None,
                 needed=2,
                 happy=3,
                 total=4):
    """
    Helper to create a single node, run it and return the instance
    spawnProcess returned (ITransport)
    """
    node_dir = join(base_dir, name)
    if web_port is None:
        web_port = ''
    if not exists(node_dir):
        print("creating", node_dir)
        mkdir(node_dir)
        done_proto = _ProcessExitedProtocol()
        args = [
            'create-node',
            '--nickname', name,
            '--introducer', introducer_furl,
            '--hostname', 'localhost',
            '--listen', 'tcp',
            '--webport', web_port,
            '--shares-needed', "{}".format(needed),
            '--shares-happy', "{}".format(happy),
            '--shares-total', "{}".format(total),
            '--helper',
        ]
        if not storage:
            args.append('--no-storage')
        args.append(node_dir)

        _tahoe_runner(done_proto, reactor, tahoe_venv, request, args)
        yield done_proto.done

        if flog_gatherer:
            config_path = join(node_dir, 'tahoe.cfg')
            config = get_config(config_path)
            set_config(config, 'node', 'log_gatherer.furl', flog_gatherer)
            write_config(config_path, config)

    magic_text = "client running"
    action_fields = {
        "action_type": u"integration:tahoe-node:service",
        "node": name,
    }
    process = yield run_tahoe_service(reactor, request, action_fields, magic_text, tahoe_venv, node_dir)
    returnValue(process)



class UnwantedFileException(Exception):
    """
    While waiting for some files to appear, some undesired files
    appeared instead (or in addition).
    """
    def __init__(self, unwanted):
        super(UnwantedFileException, self).__init__(
            u"Unwanted file appeared: {}".format(
                unwanted,
            )
        )


class ExpectedFileMismatchException(Exception):
    """
    A file or files we wanted weren't found within the timeout.
    """
    def __init__(self, path, timeout):
        super(ExpectedFileMismatchException, self).__init__(
            u"Contents of '{}' mismatched after {}s".format(path, timeout),
        )


class ExpectedFileUnfoundException(Exception):
    """
    A file or files we expected to find didn't appear within the
    timeout.
    """
    def __init__(self, path, timeout):
        super(ExpectedFileUnfoundException, self).__init__(
            u"Didn't find '{}' after {}s".format(path, timeout),
        )



class FileShouldVanishException(Exception):
    """
    A file or files we expected to disappear did not within the
    timeout
    """
    def __init__(self, path, timeout):
        super(FileShouldVanishException, self).__init__(
            u"'{}' still exists after {}s".format(path, timeout),
        )


@log_inline_callbacks(action_type=u"integration:await-file-contents", include_args=True)
def await_file_contents(path, contents, timeout=15):
    """
    Return a deferred that fires when the file at `path` (any path-like
    object) has the exact content `contents`.

    :raises ExpectedFileMismatchException: if the path doesn't have the
        expected content after the timeout.
    :raises ExpectedFileUnfoundException: if the path doesn't exist after the
        the timeout.
    """
    assert isinstance(contents, bytes), "file-contents must be bytes"
    from twisted.internet import reactor
    start_time = reactor.seconds()
    while reactor.seconds() - start_time < timeout:
        print("  waiting for '{}'".format(path))
        if exists(path):
            try:
                with open(path, 'rb') as f:
                    current = f.read()
            except IOError:
                print("IOError; trying again")
            else:
                if current == contents:
                    return
                print("  file contents still mismatched")
                # annoying if we dump huge files to console
                if len(contents) < 80:
                    print("  wanted: {}".format(contents.decode("utf8").replace('\n', ' ')))
                    print("     got: {}".format(current.decode("utf8").replace('\n', ' ')))
                log_message(
                    message_type=u"integration:await-file-contents:mismatched",
                    got=current.decode("utf8"),
                )
        else:
            log_message(
                message_type=u"integration:await-file-contents:missing",
            )
        yield twisted_sleep(reactor, 1)
    if exists(path):
        raise ExpectedFileMismatchException(path, timeout)
    raise ExpectedFileUnfoundException(path, timeout)


@inline_callbacks
def ensure_file_not_created(path, timeout=15):
    """
    Returns a deferred that fires after the given timeout, if the file has not
    appeared.

    :raises UnwantedFileException: if the file appears before the timeout.
    """
    from twisted.internet import reactor
    start_time = reactor.seconds()
    while reactor.seconds() - start_time < timeout:
        print("  waiting for '{}'".format(path))
        if exists(path):
            raise UnwantedFileException(path)
        yield twisted_sleep(reactor, 1)


def await_files_exist(paths, timeout=15, await_all=False):
    """
    wait up to `timeout` seconds for any of the paths to exist; when
    any exist, a list of all found filenames is returned. Otherwise,
    an Exception is raised
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        print("  waiting for: {}".format(' '.join(paths)))
        found = [p for p in paths if exists(p)]
        print("found: {}".format(found))
        if await_all:
            if len(found) == len(paths):
                return found
        else:
            if len(found) > 0:
                return found
        sleep(1)
    if await_all:
        nice_paths = ' and '.join(paths)
    else:
        nice_paths = ' or '.join(paths)
    raise ExpectedFileUnfoundException(nice_paths, timeout)


@inline_callbacks
def await_file_vanishes(path, timeout=10):
    from twisted.internet import reactor
    start_time = reactor.seconds()
    while reactor.seconds() - start_time < timeout:
        print("  waiting for '{}' to vanish".format(path))
        if not exists(path):
            return
        yield twisted_sleep(reactor, 1)
    raise FileShouldVanishException(path, timeout)


def node_url(node_dir, uri_fragment):
    """
    Create a fully qualified URL by reading config from `node_dir` and
    adding the `uri_fragment`
    """
    with open(join(node_dir, "node.url"), "r") as f:
        base = f.read().strip()
    url = base + uri_fragment
    return url


def _check_status(response):
    """
    Check the response code is a 2xx (raise an exception otherwise)
    """
    if response.code < 200 or response.code >= 300:
        raise ValueError(
            "Expected a 2xx code, got {}".format(response.code)
        )


@inline_callbacks
def web_get(tahoe, uri_fragment, **kwargs):
    """
    Make a GET request to the webport of `tahoe` (a `TahoeProcess`,
    usually from a fixture (e.g. `alice`). This will look like:
    `http://localhost:<webport>/<uri_fragment>`. All `kwargs` are
    passed on to `treq.get`
    """
    url = node_url(tahoe.node_dir, uri_fragment)
    resp = yield treq.get(url, **kwargs)
    _check_status(resp)
    body = yield resp.content()
    returnValue(body)


def twisted_sleep(reactor, timeout):
    """
    Return a deferred that fires after the given time.
    """
    return deferLater(reactor, timeout, lambda: None)


def sleep(timeout):
    """
    Sleep for the given amount of time, letting the pytest-twisted reactor run.

    This can only be called from the main pytest greenlet, not from the reactor
    greenlet.
    """
    from twisted.internet import reactor as _reactor
    pytest_twisted.blockon(twisted_sleep(_reactor, timeout))


@inline_callbacks
def await_client_ready(reactor, tahoe, timeout=10, liveness=60*2):
    """
    Uses the status API to wait for a client-type node (in `tahoe`, a
    `TahoeProcess` instance usually from a fixture e.g. `alice`) to be
    'ready'. A client is deemed ready if:

      - it answers `http://<node_url>/statistics/?t=json/`
      - there is at least one storage-server connected
      - it has a "last_received_data" within the last `liveness` seconds

    We will try for up to `timeout` seconds for the above conditions
    to be true. Otherwise, an exception is raised
    """
    start = reactor.seconds()
    while (reactor.seconds() - start) < float(timeout):
        try:
            data = yield web_get(tahoe, u"", params={u"t": u"json"})
            js = json.loads(data)
        except Exception as e:
            print("waiting because '{}'".format(e))
            yield twisted_sleep(reactor, 1)
            continue

        if len(js['servers']) == 0:
            print("waiting because no servers at all")
            yield twisted_sleep(reactor, 1)
            continue
        server_times = [
            server['last_received_data']
            for server in js['servers']
            if server['last_received_data'] is not None
        ]

        # check that all times are 'recent enough'
        if all([time.time() - t > liveness for t in server_times]):
            print("waiting because no server new enough")
            yield twisted_sleep(reactor, 1)
            continue

        print("finished waiting for client")
        # we have a status with at least one recently-contacted server
        returnValue(True)

    # we only fall out of the loop when we've timed out
    raise RuntimeError(
        "Waited {} seconds for {} to be 'ready' but it never was".format(
            timeout,
            tahoe,
        )
    )


def _init_magic_folder(reactor, request, base_dir, name, web_port):
    """
    Create a new magic-folder-daemon configuration

    :param reactor: The reactor to use to launch the process.
    :param request: The pytest request object to use for cleanup.
    :param base_dir: The directory in which to find a Tahoe-LAFS node.
    :param name: The alias of the Tahoe-LAFS node.

    :return Deferred[IProcessTransport]: The started process.
    """
    node_dir = join(base_dir, name)
    config_dir = join(base_dir, "magic-daemon-{}".format(name))

    args = [
        "--config", config_dir,
        "init",
        "--node-directory", node_dir,
        "--listen-endpoint", web_port,
    ]
    return _magic_folder_runner(reactor, request, name, args)


def _run_magic_folder(reactor, request, base_dir, name):
    """
    Start a magic-folder process.

    :param reactor: The reactor to use to launch the process.
    :param request: The pytest request object to use for cleanup.
    :param base_dir: The directory in which to find a Tahoe-LAFS node.
    :param name: The alias of the Tahoe-LAFS node.

    :return Deferred[IProcessTransport]: The started process.
    """
    config_dir = join(base_dir, "magic-daemon-{}".format(name))

    magic_text = "Completed initial Magic Folder setup"

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
        "--config",
        config_dir,
        # run_service will collect eliot logs from FD 3 and stderr.
        "--eliot-fd",
        "3" if sys.platform != "win32" else "2",
        "--debug",
        "--eliot-task-fields",
        json.dumps({
            "action_type": "magic-folder:service",
            "node": name,
        }),
        "run",
    ]
    action_fields = {
        "action_type": u"integration:magic-folder:service",
        "node": name,
    }
    return run_service(
        reactor,
        request,
        action_fields,
        magic_text,
        sys.executable,
        args,
    )


@inline_callbacks
def _pair_magic_folder(reactor, alice_invite, alice, bob):
    print("Joining bob to magic-folder")
    yield _command(
        "--node-directory", bob.node_directory,
        "join",
        "--author", "bob",
        "--poll-interval", "1",
        alice_invite,
        bob.magic_directory,
    )

    # before magic-folder works, we have to stop and restart (this is
    # crappy for the tests -- can we fix it in magic-folder?)
    yield bob.restart_magic_folder()

    returnValue((alice.magic_directory, bob.magic_directory))


@inline_callbacks
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


@inline_callbacks
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
    return_value = yield run_magic_folder_options(o)
    assert 0 == return_value
    returnValue(o.stdout.getvalue())


@inline_callbacks
def database_retry(reactor, seconds, f, *args, **kwargs):
    """
    Call `f` with `args` and `kwargs` .. but retry up to `seconds`
    times (1s apart) due to an sqlite3 OperationalError.

    Sometimes it's useful to get information from the config / state
    database but since production code is usually running too, it's
    possible we access it at the same time as production code. Using
    this wrapper can make the tests more reliable.
    """
    value = nothing = object()
    for _ in range(seconds):
        yield twisted_sleep(reactor, 1)
        try:
            value = yield maybeDeferred(f, *args, **kwargs)
            break
        except sqlite3.OperationalError as e:
            # since we're messing with the database while production
            # code is running, it's possible this will fail if we
            # access the database while the "real" code is also doing
            # that.
            print("sqlite3.OperationalError while running {}: {}".format(f, e))
            pass
        except KeyError:
            pass
    if value is nothing:
        raise RuntimeError(
            "Calling {} kept raising OperationError even after {} tries".format(f, seconds)
        )
    returnValue(value)
