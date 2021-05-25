from __future__ import print_function
from __future__ import unicode_literals

import sys
import json

from twisted.internet.task import (
    react,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.python import usage
from twisted.internet.defer import (
    maybeDeferred,
    inlineCallbacks,
)

from .cli import (
    _default_config_path,
    load_global_configuration,
)
from .client import (
    CannotAccessAPIError,
    MagicFolderApiError,
    create_http_client,
    create_magic_folder_client,
)


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
    res = yield options.parent.get_client().add_snapshot(
        options['folder'].decode("utf8"),
        options['file'].decode("utf8"),
    )
    print("{}".format(res), file=options.stdout)


class DumpStateOptions(usage.Options):
    optParameters = [
        ("folder", "n", None, "Name of the magic-folder whose state to dump"),
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
        public_key=config.author.verify_key.encode(encoder=HexEncoder),
    )

    print(config.name, file=options.stdout)
    print(author_info, file=options.stdout)
    print("  stash_path: {}".format(config.stash_path.path), file=options.stdout)
    print("  magic_path: {}".format(config.magic_path.path), file=options.stdout)
    print("  collective: {}".format(config.collective_dircap), file=options.stdout)
    print("  local snapshots:", file=options.stdout)
    for snap_name in config.get_all_localsnapshot_paths():
        print("    {}: ".format(snap_name), file=options.stdout)
    print("  remote snapshots:", file=options.stdout)
    for snap_name in config.get_all_remotesnapshot_paths():
        snap = config.get_remotesnapshot(snap_name)
        print("    {}: {}".format(snap_name, snap), file=options.stdout)


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
    res = yield options.parent.get_client().add_participant(
        options['folder'].decode("utf8"),
        options['author-name'].decode("utf8"),
        options['personal-dmd'].decode("utf8"),
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
    res = yield options.parent.get_client().list_participants(
        options['folder'].decode("utf8"),
    )
    print("{}".format(json.dumps(res, indent=4)), file=options.stdout)


class MagicFolderApiCommand(usage.Options):
    """
    top-level command (entry-point is "magic-folder-api")
    """
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr
    _client = None  # initialized (at most once) in get_client()

    optFlags = [
        ["version", "V", "Display version numbers."],
    ]
    optParameters = [
        ("config", "c", _default_config_path,
         "The directory containing configuration"),
    ]

    _config = None  # lazy-instantiated by .config @property

    @property
    def _config_path(self):
        """
        The FilePath where our config is located
        """
        return FilePath(self['config'])

    @property
    def config(self):
        """
        a GlobalConfigDatabase instance representing the current
        configuration location.
        """
        if self._config is None:
            try:
                self._config = load_global_configuration(self._config_path)
            except Exception as e:
                raise usage.UsageError(
                    u"Unable to load '{}': {}".format(self._config_path.path, e)
                )
        return self._config

    def get_client(self):
        if self._client is None:
            from twisted.internet import reactor
            self._client = create_magic_folder_client(
                reactor,
                self.config,
                create_http_client(reactor, self.config.api_client_endpoint),
            )
        return self._client

    subCommands = [
        ["add-snapshot", None, AddSnapshotOptions, "Add a Snapshot of a file to a magic-folder."],
        ["dump-state", None, DumpStateOptions, "Dump the local state of a magic-folder."],
        ["add-participant", None, AddParticipantOptions, "Add a Participant to a magic-folder."],
        ["list-participants", None, ListParticipantsOptions, "List all Participants in a magic-folder."],
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
def dispatch_magic_folder_api_command(args, stdout=None, stderr=None, client=None):
    """
    Run a magic-folder-api command with the given args

    :param list[str] args: arguments without the 'magic-folder-api' 0th arg

    :param stdout: file-like writable object to collect stdout (or
        None for default)

    :param stderr file-like writable object to collect stderr (or None
        for default)

    :param MagicFolderClient client: the client to use, or None to
        construct one.

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
    }[options.subCommand]

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
            print(u"   Attempted access via {}".format(options.config.api_client_endpoint), file=options.stderr)
            raise SystemExit(1)

        except MagicFolderApiError as e:
            # these kinds of errors should report via JSON from the endpoints
            print(u"{}".format(e.body), file=options.stderr)
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
