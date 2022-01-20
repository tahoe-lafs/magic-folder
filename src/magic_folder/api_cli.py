
import sys
import json
from collections import deque

import humanize

from twisted.internet.task import (
    react,
)

from autobahn.twisted.websocket import (
    WebSocketClientProtocol,
    create_client_agent,
)

from twisted.python import usage
from twisted.internet.defer import (
    maybeDeferred,
    inlineCallbacks,
)

from .cli import (
    BaseOptions,
)
from .client import (
    CannotAccessAPIError,
    MagicFolderApiError,
)
from .util.file import (
    ns_to_seconds_float,
)
from .util.eliotutil import maybe_enable_eliot_logging, with_eliot_options


class AddSnapshotOptions(usage.Options):
    optParameters = [
        ("file", "f", None, "Path of the file to add a Snapshot of"),
        ("folder", "n", None, "Name of the magic-folder to add the Snapshot to"),
    ]

    def postOptions(self):
        # required args
        if self['file'] is None:
            raise usage.UsageError("--file / -f is required")
        if self['folder'] is None:
            raise usage.UsageError("--folder / -n is required")


@inlineCallbacks
def add_snapshot(options):
    """
    Add one new Snapshot of a particular file in a particular
    magic-folder.
    """
    res = yield options.parent.client.add_snapshot(
        options['folder'],
        options['file'],
    )
    print("{}".format(res), file=options.stdout)


class DumpStateOptions(usage.Options):
    optParameters = [
        ("folder", "n", None, "Name of the magic-folder whose state to dump", str),
    ]

    def postOptions(self):
        # required args
        if self['folder'] is None:
            raise usage.UsageError("--folder / -n is required")


def dump_state(options):
    """
    Dump the database / state for a particular folder
    """
    from nacl.encoding import (
        HexEncoder,
    )

    global_config = options.parent.config
    config = global_config.get_magic_folder(options['folder'])

    author_info = "  author: {name} {public_key}".format(
        name=config.author.name,
        public_key=config.author.verify_key.encode(encoder=HexEncoder).decode("utf8"),
    )

    print(config.name, file=options.stdout)
    print(author_info, file=options.stdout)
    print("  stash_path: {}".format(config.stash_path.path), file=options.stdout)
    print("  magic_path: {}".format(config.magic_path.path), file=options.stdout)
    print("  collective: {}".format(config.collective_dircap.danger_real_capability_string()), file=options.stdout)
    print("  local snapshots:", file=options.stdout)
    for relpath in config.get_all_localsnapshot_paths():
        snap = config.get_local_snapshot(relpath)
        parents = ""
        q = deque()
        q.append(snap)
        while q:
            s = q.popleft()
            parents += "{} -> ".format(s.identifier)
            q.extend(s.parents_local)
        print("    {}: {}".format(relpath, parents[:-4]), file=options.stdout)
    print("  remote snapshots:", file=options.stdout)
    for relpath, ps, last_update, upload_duration in config.get_all_current_snapshot_pathstates():
        try:
            cap = config.get_remotesnapshot(relpath)
        except KeyError:
            cap = None
            continue
        print("    {}:".format(relpath), file=options.stdout)
        print("        cap: {}".format(cap.danger_real_capability_string()), file=options.stdout)
        print("        mtime: {}".format(ps.mtime_ns), file=options.stdout)
        print("        size: {}".format(ps.size), file=options.stdout)
        if upload_duration:
            # humans care about seconds not ns .. but we give some
            # resolution beyond 'seconds' so that speed calculations
            # (e.g. on small files) are more accurate.
            duration = ns_to_seconds_float(upload_duration)
            print("        upload time: {}".format(humanize.naturaldelta(duration)), file=options.stdout)
            print("        upload speed: {}/s".format(humanize.naturalsize(ps.size / duration)), file=options.stdout)


class AddParticipantOptions(usage.Options):
    optParameters = [
        ("folder", "n", None, "Name of the magic-folder to add a participant to"),
        ("author-name", "a", None, "Name of the new participant"),
        ("personal-dmd", "p", None, "Read-capability of the participant's Personal DMD"),
        # not yet
        # ("author-verify-key", "k", None, "Base32-encoded verify key of the new participant"),
    ]

    def postOptions(self):
        required_args = [
            ("folder", "--folder / -n is required"),
            ("author-name", "--author-name / -a is required"),
            ("personal-dmd", "--personal-dmd / -p is required"),
        ]
        for (arg, error) in required_args:
            if self[arg] is None:
                raise usage.UsageError(error)


@inlineCallbacks
def add_participant(options):
    """
    Add one new participant to an existing magic-folder
    """
    res = yield options.parent.client.add_participant(
        options['folder'],
        options['author-name'],
        options['personal-dmd'],
    )
    print("{}".format(res), file=options.stdout)


class ListParticipantsOptions(usage.Options):
    optParameters = [
        ("folder", "n", None, "Name of the magic-folder participants to list"),
    ]

    def postOptions(self):
        required_args = [
            ("folder", "--folder / -n is required"),
        ]
        for (arg, error) in required_args:
            if self[arg] is None:
                raise usage.UsageError(error)


@inlineCallbacks
def list_participants(options):
    """
    List all participants in a magic-folder
    """
    res = yield options.parent.client.list_participants(
        options['folder'],
    )
    print("{}".format(json.dumps(res, indent=4)), file=options.stdout)


class ScanOptions(usage.Options):
    optParameters = [
        ("folder", "n", None, "Name of the magic-folder to scan", str),
    ]

    def postOptions(self):
        required_args = [
            ("folder", "--folder / -n is required"),
        ]
        for (arg, error) in required_args:
            if self[arg] is None:
                raise usage.UsageError(error)

def scan(options):
    return options.parent.client.scan_folder_local(
        options['folder'],
    )


class PollOptions(usage.Options):
    optParameters = [
        ("folder", "n", None, "Name of the magic-folder to poll", str),
    ]

    def postOptions(self):
        required_args = [
            ("folder", "--folder / -n is required"),
        ]
        for (arg, error) in required_args:
            if self[arg] is None:
                raise usage.UsageError(error)

def poll(options):
    return options.parent.client.poll_folder_remote(
        options['folder'],
    )


class MonitorOptions(usage.Options):
    optFlags = [
        ["once", "", "Exit after receiving a single status message"],
    ]


class StatusProtocol(WebSocketClientProtocol):
    """
    Client-side protocol that receives status updates from
    magic-folder
    """

    def __init__(self, output, single_message):
        super(StatusProtocol, self).__init__()
        self._output = output
        self._single_message = single_message
        self._done = False

    def onMessage(self, payload, is_binary):
        if not self._done:
            print(payload.decode("utf8"), file=self._output)
        if self._single_message:
            self._done = True
            self.sendClose()


@inlineCallbacks
def monitor(options):
    """
    Print out updates from the WebSocket status API
    """

    endpoint_str = options.parent.api_client_endpoint
    websocket_uri = "{}/v1/status".format(endpoint_str.replace("tcp:", "ws://"))

    agent = options.parent.get_websocket_agent()
    proto = yield agent.open(
        websocket_uri,
        {
            "headers": {
                "Authorization": "Bearer {}".format(options.parent.api_token.decode("utf8")),
            }
        },
        lambda: StatusProtocol(
            output=options.parent.stdout,
            single_message=options['once'],
        )
    )
    yield proto.is_closed


@with_eliot_options
class MagicFolderApiCommand(BaseOptions):
    """
    top-level command (entry-point is "magic-folder-api")
    """
    _websocket_agent = None  # initialized (at most once) in get_websocket_agent()

    def get_websocket_agent(self):
        if self._websocket_agent is None:
            from twisted.internet import reactor
            self._websocket_agent = create_client_agent(reactor)
        return self._websocket_agent

    subCommands = [
        ["add-snapshot", None, AddSnapshotOptions, "Add a Snapshot of a file to a magic-folder."],
        ["dump-state", None, DumpStateOptions, "Dump the local state of a magic-folder."],
        ["add-participant", None, AddParticipantOptions, "Add a Participant to a magic-folder."],
        ["list-participants", None, ListParticipantsOptions, "List all Participants in a magic-folder."],
        ["scan", None, ScanOptions, "Scan for local changes in a magic-folder."],
        ["poll", None, PollOptions, "Poll for remote changes in a magic-folder."],
        ["monitor", None, MonitorOptions, "Monitor status updates."],
    ]
    optFlags = [
        ["debug", "d", "Print full stack-traces"],
    ]
    description = (
        "Convenience wrappers around the Magic Folder local "
        "HTTP API. Handles authentication and encoding"
    )

    @property
    def parent(self):
        return None

    @parent.setter
    def parent(self, ignored):
        pass

    def opt_version(self):
        """
        Display magic-folder version and exit.
        """
        from . import __version__
        print("magic-folder-api version {}".format(__version__), file=self.stdout)
        sys.exit(0)

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")

    def getSynopsis(self):
        return "Usage: magic-folder-api [global-options] <subcommand> [subcommand-options]"

    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += (
            "Please run e.g. 'magic-folder-api add-snapshot --help' for more "
            "details on each subcommand.\n"
        )
        return t


@inlineCallbacks
def dispatch_magic_folder_api_command(args, stdout=None, stderr=None, client=None,
                                      websocket_agent=None, config=None):
    """
    Run a magic-folder-api command with the given args

    :param list[str] args: arguments without the 'magic-folder-api' 0th arg

    :param stdout: file-like writable object to collect stdout (or
        None for default)

    :param stderr file-like writable object to collect stderr (or None
        for default)

    :param MagicFolderClient client: the client to use, or None to
        construct one.

    :param GlobalConfigurationDatabase config: a configuration, or
        None to load one.

    :param IWebSocketClientAgent websocket_agent: an Autobahn
        websocket agent to use, or None to construct one.

    :returns: a Deferred which fires with the result of doing this
        magic-folder-api (sub)command.
    """

    options = MagicFolderApiCommand()
    if stdout is not None:
        options.stdout = stdout
    if stderr is not None:
        options.stderr = stderr
    if client is not None:
        options._client = client
    if websocket_agent is not None:
        options._websocket_agent = websocket_agent
    if config is not None:
        options._config = config
    try:
        options.parseOptions(args)
    except usage.UsageError as e:
        print("Error: {}".format(e), file=options.stdout)
        # if a user just typed "magic-folder-api" don't make them re-run
        # with "--help" just to see the sub-commands they were
        # supposed to use
        if len(args) == 0:
            print(options, file=options.stdout)
        raise SystemExit(1)

    yield run_magic_folder_api_options(options)


@inlineCallbacks
def run_magic_folder_api_options(options):
    """
    Runs a magic-folder-api subcommand with the provided options.

    :param options: already-parsed options.

    :returns: a Deferred which fires with the result of doing this
        magic-folder-api (sub)command.
    """
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    main_func = {
        "add-snapshot": add_snapshot,
        "dump-state": dump_state,
        "add-participant": add_participant,
        "list-participants": list_participants,
        "scan": scan,
        "poll": poll,
        "monitor": monitor,
    }[options.subCommand]

    maybe_enable_eliot_logging(options)

    # we want to let exceptions out to the top level if --debug is on
    # because this gives better stack-traces
    if options['debug']:
        yield maybeDeferred(main_func, so)

    else:
        try:
            yield maybeDeferred(main_func, so)

        except CannotAccessAPIError as e:
            # give user more information if we can't find the daemon at all
            print(u"Error: {}".format(e), file=options.stderr)
            print(u"   Attempted access via {}".format(options.api_client_endpoint), file=options.stderr)
            raise SystemExit(1)

        except MagicFolderApiError as e:
            # these kinds of errors should report via JSON from the endpoints
            print(json.dumps(e.body), file=options.stderr)
            raise SystemExit(2)

        except Exception as e:
            print(u"Error: {}".format(e), file=options.stderr)
            raise SystemExit(3)


def _entry():
    """
    Implement the *magic-folder-api* console script declared in ``setup.py``.

    :return: ``None``
    """

    def main(reactor):
        return dispatch_magic_folder_api_command(sys.argv[1:])
    return react(main)


if __name__ == '__main__':
    # this allows one to run this like "python -m magic_folder.api_cli"
    _entry()
