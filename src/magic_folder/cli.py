import sys
import getpass
from io import StringIO

from appdirs import (
    user_config_dir,
)
from base64 import (
    urlsafe_b64decode,
)

from twisted.internet import defer
from twisted.internet.task import (
    react,
)
from twisted.internet.protocol import (
    Factory,
)
from twisted.logger import (
    globalLogBeginner,
    FileLogObserver,
    eventAsText,
)

from twisted.web.client import (
    Agent,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python import usage
from twisted.web import http

from twisted.logger import (
    Logger,
)
from treq.client import (
    HTTPClient,
)

from eliot.twisted import (
    inline_callbacks,
)

from .common import (
    valid_magic_folder_name,
    InvalidMagicFolderName,
)
from .pid import (
    check_pid_process,
)
from .client import (
    CannotAccessAPIError,
    MagicFolderApiError,
    create_magic_folder_client,
    create_http_client,
)

from .invite import (
    magic_folder_invite
)

from .list import (
    magic_folder_list
)

from .show_config import (
    magic_folder_show_config,
)
from .initialize import (
    magic_folder_initialize,
)
from .migrate import (
    magic_folder_migrate,
)
from .config import (
    load_global_configuration,
)
from .join import (
    magic_folder_join
)
from .service import (
    MagicFolderService,
)
from .util.eliotutil import (
    maybe_enable_eliot_logging,
    with_eliot_options,
)


_default_config_path = user_config_dir("magic-folder")


class ShowConfigOptions(usage.Options):
    """
    Dump current configuration as JSON.
    """

    optParameters = [
    ]
    description = (
        "Dump magic-folder configuration as JSON"
    )


def show_config(options):
    return magic_folder_show_config(
        options.parent.config,
    )


class InitializeOptions(usage.Options):
    """
    Create and initialize a new Magic Folder daemon directory (which
    will have no magic-folders in it; use "magic-folder add" for
    that).
    """

    optParameters = [
        ("listen-endpoint", "l", None, "A Twisted server string for our REST API (e.g. \"tcp:4321\")"),
        ("node-directory", "n", None, "The local path to our Tahoe-LAFS client's directory"),
        ("client-endpoint", "c", None,
         "(Optional) the Twisted client-string for our REST API (only required "
         "if auto-converting from the --listen-endpoint fails)"),
    ]

    description = (
        "Initialize a new magic-folder daemon. A single daemon may run "
        "any number of magic-folders (use \"magic-folder add\" to "
        "create a new one."
    )

    def postOptions(self):
        # required args
        if self['listen-endpoint'] is None:
            raise usage.UsageError("--listen-endpoint / -l is required")
        if self['node-directory'] is None:
            raise usage.UsageError("--node-directory / -n is required")

        # validate
        if self.parent._config_path.exists():
            raise usage.UsageError(
                "Directory '{}' already exists".format(self.parent._config_path.path)
            )


@inline_callbacks
def initialize(options):

    yield magic_folder_initialize(
        options.parent._config_path,
        options['listen-endpoint'],
        FilePath(options['node-directory']),
        options['client-endpoint'],
    )
    print(
        "Created Magic Folder daemon configuration in:\n     {}".format(options.parent._config_path.path),
        file=options.stdout,
    )


class MigrateOptions(usage.Options):
    """
    Migrate a magic-folder configuration from an existing Tahoe-LAFS
    node-directory.
    """

    optParameters = [
        ("listen-endpoint", "l", None, "A Twisted server string for our REST API (e.g. \"tcp:4321\")"),
        ("node-directory", "n", None, "A local path which is a Tahoe-LAFS node-directory"),
        ("author", "A", None, "The name for the author to use in each migrated magic-folder"),
        ("client-endpoint", "c", None,
         "(Optional) the Twisted client-string for our REST API only required "
         "if auto-converting from the listen endpoint files"),
    ]
    synopsis = (
        "\n\nCreate a new magic-folder daemon configuration in the --config "
        "path, using values from the --node-directory Tahoe-LAFS node."
    )

    def postOptions(self):
        # required args
        if self['listen-endpoint'] is None:
            raise usage.UsageError("--listen-endpoint / -l is required")
        if self['node-directory'] is None:
            raise usage.UsageError("--node-directory / -n is required")
        if self['author'] is None:
            raise usage.UsageError("--author / -a is required")

        # validate
        if self.parent._config_path.exists():
            raise usage.UsageError(
                "Directory '{}' already exists".format(self.parent._config_path.path)
            )
        if not FilePath(self['node-directory']).exists():
            raise usage.UsageError(
                "--node-directory '{}' doesn't exist".format(self['node-directory'])
            )
        if not FilePath(self['node-directory']).child("tahoe.cfg").exists():
            raise usage.UsageError(
                "'{}' doesn't look like a Tahoe node-directory (no tahoe.cfg)".format(self['node-directory'])
            )


@inline_callbacks
def migrate(options):

    config = yield magic_folder_migrate(
        options.parent._config_path,
        options['listen-endpoint'],
        FilePath(options['node-directory']),
        options['author'],
        options['client-endpoint'],
    )
    print(
        "Created Magic Folder daemon configuration in:\n     {}".format(options.parent._config_path.path),
        file=options.stdout,
    )
    print("\nIt contains the following magic-folders:", file=options.stdout)
    for name in config.list_magic_folders():
        mf = config.get_magic_folder(name)
        print("  {}: author={}".format(name, mf.author.name), file=options.stdout)


class AddOptions(usage.Options):
    local_dir = None
    synopsis = "LOCAL_DIR"
    optParameters = [
        ("poll-interval", "p", "60", "How often to ask for updates"),
        ("scan-interval", "s", "60", "Seconds between scans of local changes"),
        ("name", "n", None, "The name of this magic-folder", str),
        ("author", "A", None, "Our name for Snapshots authored here", str),
    ]
    optFlags = [
        ["disable-scanning", None, "Disable scanning for local changes."],
    ]
    description = (
        "Create a new magic-folder."
    )

    def parseArgs(self, local_dir=None):
        if local_dir is None:
            raise usage.UsageError(
                "Must specify a single argument: the local directory"
            )
        self.local_dir = FilePath(local_dir)
        if not self.local_dir.exists():
            raise usage.UsageError(
                "'{}' doesn't exist".format(local_dir)
            )
        if not self.local_dir.isdir():
            raise usage.UsageError(
                "'{}' isn't a directory".format(local_dir)
            )


    def postOptions(self):
        super(AddOptions, self).postOptions()
        _fill_author_from_environment(self)
        if self["name"] is None:
            raise usage.UsageError(
                "Must specify the --name option"
            )
        try:
            valid_magic_folder_name(self['name'])
        except InvalidMagicFolderName as e:
            raise usage.UsageError(str(e))
        try:
            self['poll-interval'] = int(self['poll-interval'])
            if self['poll-interval'] <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--poll-interval must be a positive integer"
            )
        try:
            self['scan-interval'] = int(self['scan-interval'])
            if self['scan-interval'] <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--scan-interval must be a positive integer"
            )


@inline_callbacks
def add(options):
    """
    Add a new Magic Folder
    """
    client = options.parent.client
    yield client.add_folder(
        options["name"],
        options["author"],
        options.local_dir,
        options["poll-interval"],
        None if options['disable-scanning'] else options["scan-interval"],
    )
    print("Created magic-folder named '{}'".format(options["name"]), file=options.stdout)


class StatusOptions(usage.Options):
    description = (
        "Show status of magic-folders"
    )
    optFlags = [
    ]


from autobahn.twisted.websocket import (
    create_client_agent,
)

@inline_callbacks
def status(options):
    """
    Status of magic-folders
    """
    endpoint_str = options.parent.api_client_endpoint
    websocket_uri = "{}/v1/status".format(endpoint_str.replace("tcp:", "ws://"))

    from twisted.internet import reactor
    agent = create_client_agent(reactor)
    proto = yield agent.open(
        websocket_uri,
        {
            "headers": {
                "Authorization": "Bearer {}".format(options.parent.config.api_token.decode("utf8")),
            }
        },
    )

    import json
    import humanize
    def message(payload, is_binary=False):
        now = reactor.seconds()
        data = json.loads(payload)["state"]
        for folder_name, folder in data["folders"].items():
            print('Folder "{}":'.format(folder_name))
            print("  downloads: {}".format(len(folder["downloads"])))
            if folder["downloads"]:
                print("    {}".format(
                    ", ".join(d["relpath"] for d in folder["downloads"])
                ))
            print("  uploads: {}".format(len(folder["uploads"])))
            for u in folder["uploads"]:
                queue = humanize.naturaldelta(now - u["queued-at"])
                start = " (started {} ago)".format(humanize.naturaldelta(now - u["started-at"])) if "started-at" in u else ""
                print("    {}: queued {} ago{}".format(u["relpath"], queue, start))
            print("  recent:")
            for f in folder["recent"]:
                if f["relpath"] in folder["uploads"] or f["relpath"] in folder["downloads"]:
                    continue
                if f["modified"] is not None:
                    modified_text = "modified {} ago".format(
                        humanize.naturaldelta(now - f["modified"])
                    )
                else:
                    modified_text = "[deleted]"
                print("    {}: {}{} (updated {} ago)".format(
                    f["relpath"],
                    "[CONFLICT] " if f["conflicted"] else "",
                    modified_text,
                    humanize.naturaldelta(now - f["last-updated"]),
                ))
            if folder["errors"]:
                print("Errors:")
                for e in folder["errors"]:
                    print("  {}: {}".format(e["timestamp"], e["summary"]))
    proto.on('message', message)

    yield proto.is_closed


class ListOptions(usage.Options):
    description = (
        "List all magic-folders this client has joined"
    )
    optFlags = [
        ("json", "", "Produce JSON output"),
        ("include-secret-information", "", "Include sensitive secret data too"),
    ]


@inline_callbacks
def list_(options):
    """
    List existing magic-folders.
    """
    from twisted.internet import reactor
    yield magic_folder_list(
        reactor,
        options.parent.config,
        options.parent.client,
        options.stdout,
        options["json"],
        options["include-secret-information"],
    )


class InviteOptions(usage.Options):
    nickname = None
    synopsis = "NICKNAME\n\nProduce an invite code for a new device called NICKNAME"
    stdin = StringIO(u"")
    optParameters = [
        ("name", "n", None, "Name of an existing magic-folder"),
    ]
    description = (
        "Invite a new participant to a given magic-folder. The resulting "
        "invite-code that is printed is secret information and MUST be "
        "transmitted securely to the invitee."
    )

    def parseArgs(self, nickname):
        super(InviteOptions, self).parseArgs()
        self.nickname = nickname

    def postOptions(self):
        if self["name"] is None:
            raise usage.UsageError(
                "Must specify the --name option"
            )


@inline_callbacks
def invite(options):
    from twisted.internet import reactor
    treq = HTTPClient(Agent(reactor))

    invite_code = yield magic_folder_invite(
        options.parent.config,
        options['name'],
        options.nickname,
        treq,
    )
    print(u"{}".format(invite_code), file=options.stdout)


class JoinOptions(usage.Options):
    synopsis = "INVITE_CODE LOCAL_DIR"
    dmd_write_cap = ""
    magic_readonly_cap = ""
    optParameters = [
        ("poll-interval", "p", "60", "How often to ask for updates"),
        ("name", "n", None, "Name for the new magic-folder"),
        ("author", "A", None, "Author name for Snapshots in this magic-folder"),
    ]

    def parseArgs(self, invite_code, local_dir):
        super(JoinOptions, self).parseArgs()

        try:
            if int(self['poll-interval']) <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--poll-interval must be a positive integer"
            )
        self.local_dir = FilePath(local_dir)
        if not self.local_dir.exists():
            raise usage.UsageError(
                "'{}' doesn't exist".format(local_dir)
            )
        if not self.local_dir.isdir():
            raise usage.UsageError(
                "'{}' isn't a directory".format(local_dir)
            )
        self.invite_code = invite_code

    def postOptions(self):
        super(JoinOptions, self).postOptions()
        _fill_author_from_environment(self)
        if self["name"] is None:
            raise usage.UsageError(
                "Must specify the --name option"
            )


def join(options):
    """
    ``magic-folder join`` entrypoint.
    """
    return magic_folder_join(
        options.parent.config,
        options.invite_code,
        options.local_dir,
        options["name"],
        options["poll-interval"],
        options["author"],
    )


def _fill_author_from_environment(options):
    """
    Internal helper. Fills in an `author` option from the environment
    if it is not already set.
    """
    if options['author'] is None:
        options['author'] = getpass.getuser()
        if options['author'] is None:
            raise usage.UsageError(
                "--author not provided and could not determine a username"
            )


class LeaveOptions(usage.Options):
    description = "Remove a magic-folder and forget all state"
    optFlags = [
        ("really-delete-write-capability", "", "Allow leaving a folder created on this device"),
    ]
    optParameters = [
        ("name", "n", None, "Name of magic-folder to leave", str),
    ]

    def postOptions(self):
        super(LeaveOptions, self).postOptions()
        if self["name"] is None:
            raise usage.UsageError(
                "Must specify the --name option"
            )


@inline_callbacks
def leave(options):
    client = options.parent.client
    try:
        yield client.leave_folder(
            options["name"],
            really_delete_write_capability=options["really-delete-write-capability"],
        )
    except MagicFolderApiError as e:
        print("Error: {}".format(e), file=options.stderr)
        if e.code == http.CONFLICT:
            print(
                "If you really want to delete it, pass --really-delete-write-capability",
                file=options.stderr,
            )
        if "details" in e.body:
            for path, error in e.body["details"]:
                print("{}: {}".format(path, error), file=options.stderr)
        raise SystemExit(1)


class RunOptions(usage.Options):
    optParameters = [
    ]


@defer.inlineCallbacks
def run(options):
    """
    This is the long-running magic-folders function which performs
    synchronization between local and remote folders.
    """
    from twisted.internet import reactor

    # being logging to stdout
    def event_to_string(event):
        # "t.i.protocol.Factory" produces a bunch of 'starting' and
        # 'stopping' messages that are quite noisy in the logs (and
        # don't provide useful information); skip them.
        if isinstance(event.get("log_source", None), Factory):
            return
        # docstring seems to indicate eventAsText() includes a
        # newline, but it .. doesn't
        return u"{}\n".format(
            eventAsText(event, includeSystem=False)
        )
    globalLogBeginner.beginLoggingTo([
        FileLogObserver(options.stdout, event_to_string),
    ])

    config = options.parent.config
    pidfile = config.basedir.child("pid")

    # check our pidfile to see if another process is running (if not,
    # write our PID to it)
    with check_pid_process(pidfile, Logger()):
        # start the daemon services
        service = MagicFolderService.from_config(reactor, config)
        yield service.run()


@with_eliot_options
class BaseOptions(usage.Options):
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    optFlags = [
        ["version", "V", "Display version numbers."],
    ]
    optParameters = [
        ("config", "c", _default_config_path,
         "The directory containing configuration"),
    ]

    _config = None  # lazy-instantiated by .config @property
    _http_client = None  # lazy-initalized by .client @property
    _client = None  # lazy-instantiated by .client @property

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
                    u"Unable to load configuration: {}".format(e)
                )
        return self._config

    @property
    def api_client_endpoint(self):
        """
        retrieve the client API endpoint (from the filesystem, not config
        database) falling back to the database
        """
        try:
            with self._config_path.child("api_client_endpoint").open("rb") as f:
                endpoint_str = f.read().decode("utf8").strip()
                if endpoint_str == "not running":
                    raise Exception("Service not running.")
                return endpoint_str
        except Exception:
            if self.config.api_client_endpoint is None:
                raise Exception("Service not running.")
            return self.config.api_client_endpoint

    @property
    def api_token(self):
        """
        retrieve the client API token (from the filesystem, not config
        database) falling back to the database
        """
        try:
            with self._config_path.child("api_token").open("rb") as f:
                data = f.read()
        except Exception:
            data = self.config.api_token
        # confirm the data is syntactially correct: it is 32 bytes
        # of url-safe base64-encoded random data
        if len(urlsafe_b64decode(data)) != 32:
            raise Exception("Incorrect token data")
        return data


    @property
    def client(self):
        if self._client is None:
            from twisted.internet import reactor
            endpoint_str = self.api_client_endpoint

            if self._http_client is None:
                self._http_client = create_http_client(reactor, endpoint_str)
            self._client = create_magic_folder_client(
                reactor,
                self.config,
                self._http_client,
            )
        return self._client


class MagicFolderCommand(BaseOptions):

    subCommands = [
        ["init", None, InitializeOptions, "Initialize a Magic Folder daemon."],
        ["migrate", None, MigrateOptions, "Migrate a Magic Folder from Tahoe-LAFS 1.14.0 or earlier"],
        ["show-config", None, ShowConfigOptions, "Dump configuration as JSON"],
        ["add", None, AddOptions, "Add a new Magic Folder."],
        ["invite", None, InviteOptions, "Invite someone to a Magic Folder."],
        ["join", None, JoinOptions, "Join a Magic Folder."],
        ["leave", None, LeaveOptions, "Leave a Magic Folder."],
        ["list", None, ListOptions, "List Magic Folders configured in this client."],
        ["run", None, RunOptions, "Run the Magic Folders daemon process."],
        ["status", None, StatusOptions, "Show the current status of a folder."],
    ]
    optFlags = [
        ["debug", "d", "Print full stack-traces"],
        ("coverage", None, "Enable coverage measurement."),
    ]
    description = (
        "A magic-folder has an owner who controls the writecap "
        "containing a list of nicknames and readcaps. The owner can invite "
        "new participants. Every participant has the writecap for their "
        "own folder (the corresponding readcap is in the master folder). "
        "All clients download files from all other participants using the "
        "readcaps contained in the master magic-folder directory."
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
        print("Magic Folder version {}".format(__version__))
        sys.exit(0)

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")

    def getSynopsis(self):
        return "Usage: magic-folder [global-options] <subcommand> [subcommand-options]"

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += (
            "Please run e.g. 'magic-folder add --help' for more "
            "details on each subcommand.\n"
        )
        return t


subDispatch = {
    "init": initialize,
    "migrate": migrate,
    "show-config": show_config,
    "add": add,
    "invite": invite,
    "join": join,
    "leave": leave,
    "list": list_,
    "status": status,
    "run": run,
}


def dispatch_magic_folder_command(args):
    """
    Run a magic-folder command with the given args

    :returns: a Deferred which fires with the result of doing this
        magic-folder (sub)command.
    """
    options = MagicFolderCommand()
    try:
        options.parseOptions(args)
    except usage.UsageError as e:
        print("Error: {}".format(e))
        # if a user just typed "magic-folder" don't make them re-run
        # with "--help" just to see the sub-commands they were
        # supposed to use
        if len(sys.argv) == 1:
            print(options)
        raise SystemExit(1)

    return run_magic_folder_options(options)


# If `--eliot-task-fields` is passed, then `maybe_enable_eliot_logging` will
# start an action that is meant to be a parent of *all* logs this process
# generates. Since we call that function in this generator, if we used
# `eliot.inline_callbacks` here, eliot would remove that action context from
# it stack when when we yield to reactor.
@defer.inlineCallbacks
def run_magic_folder_options(options):
    """
    Runs a magic-folder subcommand with the provided options.

    :param options: already-parsed options.

    :returns: a Deferred which fires with the result of doing this
        magic-folder (sub)command.
    """
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr

    maybe_enable_eliot_logging(options)

    f = subDispatch[options.subCommand]

    # we want to let exceptions out to the top level if --debug is on
    # because this gives better stack-traces
    if options['debug']:
        yield f(so)

    else:
        try:
            yield f(so)

        except CannotAccessAPIError as e:
            # give user more information if we can't find the daemon at all
            print(u"Error: {}".format(e), file=options.stderr)
            print(u"   Attempted access via {}".format(options.config.api_client_endpoint))
            raise SystemExit(1)

        except Exception as e:
            print(u"Error: {}".format(e), file=options.stderr)
            raise SystemExit(1)


def _entry():
    """
    Implement the *magic-folder* console script declared in ``setup.py``.

    :return: ``None``
    """
    def main(reactor):
        return dispatch_magic_folder_command(sys.argv[1:])
    return react(main)
