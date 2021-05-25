from __future__ import print_function
from __future__ import unicode_literals

import sys
import getpass
from six.moves import (
    StringIO as MixedIO,
)

from appdirs import (
    user_config_dir,
)

import attr

from twisted.internet.endpoints import (
    serverFromString,
)
from twisted.internet.task import (
    react,
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
from twisted.application.service import (
    MultiService,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.defer import (
    maybeDeferred,
    gatherResults,
    Deferred,
    inlineCallbacks,
    returnValue,
)

from treq.client import (
    HTTPClient,
)

from eliot.twisted import (
    inline_callbacks,
)

from allmydata.util.encodingutil import (
    argv_to_unicode,
    to_bytes,
)

from allmydata.client import (
    read_config,
)

from .magic_folder import (
    MagicFolder,
)
from .web import (
    magic_folder_web_service,
)
from .client import (
    create_http_client,
    CannotAccessAPIError,
)

from .invite import (
    magic_folder_invite
)

from .list import (
    magic_folder_list
)

from .create import (
    magic_folder_create
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
from .tahoe_client import (
    create_tahoe_client,
)

from .join import (
    magic_folder_join
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


@inlineCallbacks
def initialize(options):

    yield magic_folder_initialize(
        options.parent._config_path,
        options['listen-endpoint'].decode("utf8"),
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


@inlineCallbacks
def migrate(options):

    config = yield magic_folder_migrate(
        options.parent._config_path,
        options['listen-endpoint'].decode("utf8"),
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
        ("name", "n", None, "The name of this magic-folder"),
        ("author", "A", None, "Our name for Snapshots authored here"),
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
            if int(self['poll-interval']) <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--poll-interval must be a positive integer"
            )


@inlineCallbacks
def add(options):
    """
    Add a new Magic Folder
    """
    from twisted.internet import reactor
    treq = HTTPClient(Agent(reactor))
    client = create_tahoe_client(options.parent.config.tahoe_client_url, treq)
    yield magic_folder_create(
        options.parent.config,
        argv_to_unicode(options["name"]),
        argv_to_unicode(options["author"]),
        options.local_dir,
        options["poll-interval"],
        client,
    )
    print("Created magic-folder named '{}'".format(options["name"]), file=options.stdout)


class ListOptions(usage.Options):
    description = (
        "List all magic-folders this client has joined"
    )
    optFlags = [
        ("json", "", "Produce JSON output"),
        ("include-secret-information", "", "Include sensitive secret data too"),
    ]


@inlineCallbacks
def list_(options):
    """
    List existing magic-folders.
    """
    from twisted.internet import reactor
    yield magic_folder_list(
        reactor,
        options.parent.config,
        create_http_client(reactor, options.parent.config.api_client_endpoint),
        options.stdout,
        options["json"],
        options["include-secret-information"],
    )


class InviteOptions(usage.Options):
    nickname = None
    synopsis = "NICKNAME\n\nProduce an invite code for a new device called NICKNAME"
    stdin = MixedIO(u"")
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
        self.nickname = argv_to_unicode(nickname)

    def postOptions(self):
        if self["name"] is None:
            raise usage.UsageError(
                "Must specify the --name option"
            )


@inlineCallbacks
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
        self.invite_code = to_bytes(argv_to_unicode(invite_code))

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
        ("name", "n", None, "Name of magic-folder to leave"),
    ]

    def postOptions(self):
        super(LeaveOptions, self).postOptions()
        if self["name"] is None:
            raise usage.UsageError(
                "Must specify the --name option"
            )


def leave(options):
    try:
        folder_config = options.parent.config.get_magic_folder(options["name"])
    except ValueError:
        raise usage.UsageError(
            "No such magic-folder '{}'".format(options["name"])
        )

    if folder_config.is_admin():
        if not options["really-delete-write-capability"]:
            print(
                "ERROR: magic folder '{}' holds a write capability"
                ", not deleting.".format(options["name"]),
                file=options.stderr,
            )
            print(
                "If you really want to delete it, pass --really-delete-write-capability",
                file=options.stderr,
            )
            return 1

    fails = options.parent.config.remove_magic_folder(options["name"])
    if fails:
        print(
            "ERROR: Problems while removing state directories:",
            file=options.stderr,
        )
        for path, error in fails:
            print("{}: {}".format(path, error), file=options.stderr)
        return 1

    return 0


class RunOptions(usage.Options):
    optParameters = [
    ]


def run(options):
    """
    This is the long-running magic-folders function which performs
    synchronization between local and remote folders.
    """
    from twisted.internet import reactor

    # being logging to stdout
    def event_to_string(event):
        # docstring seems to indicate eventAsText() includes a
        # newline, but it .. doesn't
        return u"{}\n".format(
            eventAsText(event, includeSystem=False)
        )
    globalLogBeginner.beginLoggingTo([
        FileLogObserver(options.stdout, event_to_string),
    ])

    # start the daemon services
    config = options.parent.config
    service = MagicFolderService.from_config(reactor, config)
    return service.run()


@inline_callbacks
def poll(label, operation, reactor):
    while True:
        print("Polling {}...".format(label))
        status, message = yield operation()
        if status:
            print("{}: {}, done.".format(label, message))
            break
        print("Not {}: {}".format(label, message))
        yield deferLater(reactor, 1.0, lambda: None)


@attr.s
class MagicFolderService(MultiService):
    """
    :ivar reactor: the Twisted reactor to use

    :ivar GlobalConfigDatabase config: our system configuration
    """
    reactor = attr.ib()
    config = attr.ib()
    tahoe_client = attr.ib(default=None)

    def __attrs_post_init__(self):
        MultiService.__init__(self)
        if self.tahoe_client is None:
            self.tahoe_client = create_tahoe_client(
                self.config.tahoe_client_url,
                HTTPClient(Agent(self.reactor)),
            )
        self._listen_endpoint = serverFromString(
            self.reactor,
            self.config.api_endpoint,
        )
        web_service = magic_folder_web_service(
            self._listen_endpoint,
            self.config,
            self,
            self._get_auth_token,
            self.tahoe_client,
        )
        web_service.setServiceParent(self)

        # We can create the services for all configured folders right now.
        # They won't do anything until they are started which won't happen
        # until this service is started.
        self._create_magic_folder_services()

    def _create_magic_folder_services(self):
        """
        Create all of the child magic folder services and attach them to this
        service.
        """
        for name in self.config.list_magic_folders():
            mf = MagicFolder.from_config(
                self.reactor,
                self.tahoe_client,
                name,
                self.config,
            )
            mf.setServiceParent(self)

    def _iter_magic_folder_services(self):
        """
        Iterate over all of the magic folder services which are children of this
        service.
        """
        for service in self:
            if isinstance(service, MagicFolder):
                yield service

    def get_folder_service(self, folder_name):
        """
        Look up a ``MagicFolder`` by its name.

        :param unicode folder_name: The name of the magic-folder to retrieve.

        :raise KeyError: If no magic-folder with a matching name is found.

        :return MagicFolder: The service for the matching magic-folder.
        """
        for service in self._iter_magic_folder_services():
            if service.folder_name == folder_name:
                return service
        raise KeyError(folder_name)

    def _get_auth_token(self):
        return self.config.api_token

    @classmethod
    def from_config(cls, reactor, config):
        """
        Create a new service given a reactor and global configuration.

        :param GlobalConfigDatabase config: config to use
        """
        return cls(
            reactor,
            config,
        )

    def _when_connected_enough(self):
        # start processing the upload queue when we've connected to
        # enough servers
        tahoe_config = read_config(self.config.tahoe_node_directory.path, "portnum")
        threshold = int(tahoe_config.get_config("client", "shares.needed"))

        @inline_callbacks
        def enough():
            try:
                welcome_body = yield self.tahoe_client.get_welcome()
            except Exception:
                returnValue((False, "Failed to get welcome page"))

            servers = welcome_body[u"servers"]
            connected_servers = [
                server
                for server in servers
                if server["connection_status"].startswith("Connected ")
            ]

            message = "Found {} of {} connected servers (want {})".format(
                len(connected_servers),
                len(servers),
                threshold,
            )

            if len(connected_servers) < threshold:
                returnValue((False, message))
            returnValue((True, message))
        return poll("connected enough", enough, self.reactor)

    def run(self):
        d = self._when_connected_enough()
        d.addCallback(lambda ignored: self.startService())
        d.addCallback(lambda ignored: Deferred())
        return d

    def startService(self):
        MultiService.startService(self)

        ds = []
        for magic_folder in self._iter_magic_folder_services():
            ds.append(magic_folder.ready())
        # The integration tests look for this message.  You cannot get rid of
        # it (without also changing the tests).
        print("Completed initial Magic Folder setup")
        self._starting = gatherResults(ds)

    def stopService(self):
        self._starting.cancel()
        MultiService.stopService(self)
        return self._starting


class BaseOptions(usage.Options):
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
                    u"Unable to load configuration: {}".format(e)
                )
        return self._config


class MagicFolderCommand(BaseOptions):
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

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
    "run": run,
}


@inlineCallbacks
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

    yield run_magic_folder_options(options)


@inlineCallbacks
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
    f = subDispatch[options.subCommand]

    # we want to let exceptions out to the top level if --debug is on
    # because this gives better stack-traces
    if options['debug']:
        yield maybeDeferred(f, so)

    else:
        try:
            yield maybeDeferred(f, so)

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
    from eliot import to_file
    from os import getpid
    to_file(open("magic-folder-cli.{}.eliot".format(getpid()), "w"))

    def main(reactor):
        return dispatch_magic_folder_command(sys.argv[1:])
    return react(main)
