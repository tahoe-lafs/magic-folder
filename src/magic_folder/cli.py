import sys
import getpass
import json
import humanize
from io import StringIO
from collections import defaultdict
from datetime import datetime

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
    Protocol,
)
from twisted.internet.stdio import (
    StandardIO,
)
from twisted.logger import (
    globalLogBeginner,
    FileLogObserver,
    eventAsText,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python import usage
from twisted.web import http
from twisted.logger import (
    Logger,
)
from autobahn.twisted.websocket import (
    create_client_agent,
)
from wormhole.cli.public_relay import (
    RENDEZVOUS_RELAY,
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
    describe_experimental_features,
    is_valid_experimental_feature,
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
        ("mailbox", "m", RENDEZVOUS_RELAY,
         "The URL upon which to contact the Magic Wormhole mailbox service"),
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
        options['mailbox'],
    )
    print(
        "Created Magic Folder daemon configuration in:\n     {}".format(options.parent._config_path.path),
        file=options.stdout,
    )


class ConfigOptions(usage.Options):
    """
    Change configuration options in an existing Magic Folder daemon
    directory.
    """

    optParameters = [
        # should include these, probably "for completeness" but
        # leaving out for now
        # ("listen-endpoint", "l", None, "A Twisted server string for our REST API (e.g. \"tcp:4321\")"),
        # ("client-endpoint", "c", None,
        #  "The Twisted client-string for our REST API (only required if auto-converting"
        #  " from the --listen-endpoint fails)"),
        ("enable", None, None, "Enable experimental feature"),
        ("disable", None, None, "Disable experimental feature"),
    ]

    optFlags = [
        ("features", None, "List available experimental features"),
    ]

    description = (
        "Change configuration options"
    )


@inline_callbacks
def set_config(options):
    """
    Change configuration options
    """
    if options["features"]:
        print(describe_experimental_features(), file=options.stdout)
        return

    try:
        if options["enable"]:
            yield options.parent.client.enable_feature(options["enable"])
        elif options["disable"]:
            yield options.parent.client.disable_feature(options["disable"])
        else:
            print(options, file=options.stdout)
    except MagicFolderApiError as err:
        if err.code >= 400 and err.code < 500:
            print("Error: {}".format(err.reason), file=options.stderr)
        else:
            raise


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


@inline_callbacks
def status(options):
    """
    Status of magic-folders
    """
    endpoint_str = options.parent.api_client_endpoint
    websocket_uri = "{}/v1/status".format(endpoint_str.replace("tcp:", "ws://"))
    agent = options.parent.websocket_agent
    out = options.parent.stdout
    reactor = options.parent.reactor

    def fresh_folder():
        """
        Initial state for an as-yet-unseen folder
        """
        return {
            "last-scan": None,
            "last-poll": None,
            "uploads": defaultdict(dict),
            "downloads": defaultdict(dict),
            "errors": [],
        }

    # the daemon sends us _updates_, so we must track our state
    state = {
        "folders": defaultdict(fresh_folder),
        "tahoe": {
            "happy": False,
            "connected": None,
            "desired": None,
        },
    }

    # lambdas can't assign things
    def copy_state(dest, src, keys):
        for k in keys:
            dest[k] = src[k]

    def error(e):
        """
        Handle a kind=error message
        """
        state["folders"][e["folder"]]["errors"].append({
            "timestamp": e["timestamp"],
            "summary": e["summary"],
        })

    def upload(e):
        """
        Handle all events related to uploads
        """
        ours = state["folders"][e["folder"]]["uploads"]
        if e["kind"] == "upload-queued":
            ours[e["relpath"]]["queued-at"] = e["timestamp"]
        elif e["kind"] == "upload-started":
            ours[e["relpath"]]["started-at"] = e["timestamp"]

        # XXX might want a final "upload-stats" sort of thing as well?
        elif e["kind"] == "upload-finished":
            del ours[e["relpath"]]

    def download(e):
        """
        Handle all events related to downloads
        """
        ours = state["folders"][e["folder"]]["downloads"]
        if e["kind"] == "dowload-queued":
            ours[e["relpath"]]["queued-at"] = e["timestamp"]
        elif e["kind"] == "download-started":
            ours[e["relpath"]]["started-at"] = e["timestamp"]

        # XXX might want a final "download-stats" sort of thing as well?
        if e["kind"] == "download-finished":
            del ours[e["relpath"]]

    # it might be interesting to have a status command that simply
    # dumps event as they arrive. the original (state-message-based)
    # UI did dump the "whole state at once" so at the very least this
    # serves as proof a UI _can_ do that properly with this events API
    folders = yield options.parent.client.list_folders()
    for folder_name in folders.keys():
        print("  recent:", file=out)
        recent = yield options.parent.client.recent_changes(folder_name, 3)
        now = reactor.seconds()
        for f in recent:
            if f["modified"] is not None:
                modified_text = "modified {} ago".format(
                    humanize.naturaldelta(now - f["modified"])
                )
            else:
                modified_text = "[deleted]"
            print(
                "    {}: {}{} (updated {} ago)".format(
                    f["relpath"],
                    "[CONFLICT] " if f["conflicted"] else "",
                    modified_text,
                    humanize.naturaldelta(now - f["last-updated"]),
                ),
                file=out,
            )

    # based on the "kind" of event, we update our state
    updates = {
        "scan-completed": lambda e: copy_state(state["folders"][e["folder"]], e, {"last-scan"}),
        "poll-completed": lambda e: copy_state(state["folders"][e["folder"]], e, {"last-poll"}),
        "tahoe-connection-changed": lambda e: copy_state(state["tahoe"], e, {"happy", "connected", "desired"}),
        "error-occurred": error,
        "folder-added": lambda _: None,
        "folder-deleted": lambda _: None,
        "upload-queued": upload,
        "upload-started": upload,
        "upload-finished": upload,
        "download-queued": download,
        "download-started": download,
        "download-finished": download,
    }

    def message(payload, is_binary=False):
        """
        onMessage handler for WebSocket payloads.

        The first message should contain all events necessary for our
        state to match the actual state.
        """
        now = reactor.seconds()
        data = json.loads(payload)

        for event in data["events"]:
            assert "kind" in event and event["kind"] in updates, "weird: {}".format(event["kind"])
            updates[event["kind"]](event)

        for folder_name, folder in state["folders"].items():
            print('Folder "{}":'.format(folder_name), file=out)
            print("  downloads: {}".format(len(folder["downloads"])), file=out)
            if folder["downloads"]:
                print(
                    "    {}".format(
                        ", ".join(folder["downloads"].keys())
                    ),
                    file=out,
                )
            print("  uploads: {}".format(len(folder["uploads"])), file=out)
            for relpath, u in folder["uploads"].items():
                queue = humanize.naturaldelta(now - u["queued-at"])
                start = " (started {} ago)".format(humanize.naturaldelta(now - u["started-at"])) if "started-at" in u else ""
                print("    {}: queued {} ago{}".format(relpath, queue, start), file=out)

            if folder["errors"]:
                print("Errors:", file=out)
                # filter out duplicates
                different_errors = {}
                for e in folder["errors"]:
                    summary = e["summary"]
                    if summary in different_errors:
                        different_errors[summary]["timestamps"].append(e["timestamp"])
                    else:
                        different_errors[summary] = {
                            "summary": summary,
                            "timestamps": [e["timestamp"]],
                        }
                for summary, e in different_errors.items():
                    if len(e["timestamps"]) == 1:
                        ts = humanize.naturaldelta(now - e["timestamps"][0])
                    else:
                        ts = "{} times between {} and {}".format(
                            len(e["timestamps"]),
                            humanize.naturaldelta(now - e["timestamps"][-1]),
                            humanize.naturaldelta(now - e["timestamps"][0]),
                        )
                    print("  {} ({} ago)".format(summary, ts), file=out)
            if folder["last-scan"] is not None:
                print(
                    "Last scan for uploads at {}".format(
                        datetime.fromtimestamp(folder["last-scan"]),
                    ),
                    file=out,
                )
            if folder["last-poll"] is not None:
                print(
                    "Last poll for downloads at {}".format(
                        datetime.fromtimestamp(folder["last-poll"]),
                    ),
                    file=out,
                )
        print(
            "Tahoe is {is_happy}: connected to {connected} servers (want {desired})".format(
                is_happy="happy" if state["tahoe"]["happy"] else "UNHAPPY",
                **state["tahoe"]
            ),
            file=out,
        )

    # note: can't do async work between creating the proto and adding
    # the on-message handler, or you might miss an update
    proto = yield agent.open(
        websocket_uri,
        {
            "headers": {
                "Authorization": "Bearer {}".format(options.parent.config.api_token.decode("utf8")),
            }
        },
    )
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


def experimental(name):
    """
    A class-decorator that marks an Options as related to an
    experimental feature.

    This makes the usage information display the experimental status
    and how to turn it on.
    """
    assert is_valid_experimental_feature(name)

    def decorator(klass):
        orig_usage = klass.getUsage

        def usage_wrapper(self, *args, **kw):
            usage = orig_usage(self, *args, **kw)
            enabled = self.parent.config.feature_enabled(name)
            return usage + (
                "\nThis is an experimental feature. Turn it {} with:"
                "\n    magic-folder --config {} set-config --{} {}".format(
                    "off" if enabled else "on",
                    self.parent["config"],
                    "disable" if enabled else "enable",
                    name,
                )
            )
            return usage
        klass.getUsage = usage_wrapper
        return klass
    return decorator


@experimental("invites")
class InviteOptions(usage.Options):
    participant_name = None
    synopsis = "NAME\n\nProduce an invite code for a new device called NAME"
    stdin = StringIO(u"")
    optParameters = [
        ("folder", "n", None, "Name of an existing magic-folder"),
        ("mode", "m", "read-write", "Mode of the invited device: read-write or read-only"),
    ]
    description = (
        "Invite a new participant to a given magic-folder. The resulting "
        "invite-code that is printed is secret information and MUST be "
        "transmitted securely to the invitee."
    )

    def parseArgs(self, name):
        super(InviteOptions, self).parseArgs()
        self.participant_name = name

    def postOptions(self):
        valid_modes = ["read-write", "read-only"]
        if self["mode"] not in valid_modes:
            raise usage.UsageError(
                "Mode must be one of: {}".format(", ".join(valid_modes))
            )
        if self["folder"] is None:
            raise usage.UsageError(
                "Must specify the --folder option"
            )


@inline_callbacks
def invite(options):
    client = options.parent.client

    # do HTTP request to the API
    data = yield client.invite(options["folder"], options.participant_name, options["mode"])
    print(u"Secret invite code: {}".format(data["wormhole-code"]), file=options.stdout)
    print(u"  waiting for {} to accept...".format(data["participant-name"]), file=options.stdout)
    options.stdout.flush()

    try:
        # second HTTP request to the API
        res = yield client.invite_wait(options["folder"], data["id"])
        print("Successfully added as '{}'".format(res["participant-name"]), file=options.stdout)
    except MagicFolderApiError as e:
        print("Error: {}".format(e.reason), file=options.stderr)


@experimental("invites")
class JoinOptions(usage.Options):
    synopsis = "INVITE_CODE LOCAL_DIR"
    dmd_write_cap = ""
    magic_readonly_cap = ""
    optParameters = [
        ("poll-interval", "p", "60", "How often to look for remote updates"),
        ("scan-interval", "s", "60", "How often to detect local changes"),
        ("name", "n", None, "Name for the new magic-folder"),
        ("author", "A", None, "Author name for Snapshots in this magic-folder"),
    ]
    optFlags = [
        ["disable-scanning", None, "Disable scanning for local changes."],
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
        try:
            if int(self['scan-interval']) <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--scan-interval must be a positive integer"
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


@inline_callbacks
def join(options):
    """
    ``magic-folder join`` entrypoint.
    """
    ans = yield options.parent.client.join(
        options["name"],
        options.invite_code,
        options.local_dir,
        options["author"],
        int(options["poll-interval"]),
        None if options['disable-scanning'] else int(options["scan-interval"]),
    )
    if ans["success"]:
        print("Successfully joined as '{}'".format(ans["participant-name"]))
    else:
        print("Error joining: {}".format(ans["error"]))


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


def on_stdin_close(reactor, fn):
    """
    Arrange for the function `fn` to run when our stdin closes
    """
    when_closed_d = defer.Deferred()

    class WhenClosed(Protocol):
        """
        Notify a Deferred when our connection is lost .. as this is passed
        to twisted's StandardIO class, it is used to detect our parent
        going away.
        """

        def connectionLost(self, reason):
            when_closed_d.callback(None)

    def on_close(arg):
        try:
            fn()
        except Exception:
            # for our "exit" use-case, this will _mostly_ just be
            # ReactorNotRunning (because we're already shutting down
            # when our stdin closes) but no matter what "bad thing"
            # happens we just want to ignore it.
            pass
        return arg

    when_closed_d.addBoth(on_close)
    # we don't need to do anything with this instance because it gets
    # hooked into the reactor and thus remembered (but we return the
    # proto for Windows testing purposes)
    return StandardIO(
        proto=WhenClosed(),
        reactor=reactor,
    )


@defer.inlineCallbacks
def run(options):
    """
    This is the long-running magic-folders function which performs
    synchronization between local and remote folders.
    """
    from twisted.internet import reactor

    # When our stdin closes then we exit. This helps support parent
    # processes cleaning up properly, even when they exit without
    # ability to run shutdown code
    on_stdin_close(reactor, reactor.stop)

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
    pidfile = config.basedir.child("running.process")

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
    _ws_agent = None  # lazy-instantiated by .websocket_agent

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
    def websocket_agent(self):
        """
        An implementor of IWebSocketClientAgent from autobahn
        """
        if self._ws_agent is None:
            from twisted.internet import reactor
            self._ws_agent = create_client_agent(reactor)
        return self._ws_agent

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
        ["set-config", None, ConfigOptions, "Change configuration options."],
        ["migrate", None, MigrateOptions, "Migrate a Magic Folder from Tahoe-LAFS 1.14.0 or earlier"],
        ["show-config", None, ShowConfigOptions, "Dump configuration as JSON"],
        ["add", None, AddOptions, "Add a new Magic Folder."],
        ["invite", None, InviteOptions, "Invite someone to a Magic Folder. (Experimental)"],
        ["join", None, JoinOptions, "Join a Magic Folder. (Experimental)"],
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
    "set-config": set_config,
    "add": add,
    "invite": invite,
    "join": join,
    "leave": leave,
    "list": list_,
    "status": status,
    "run": run,
}


def dispatch_magic_folder_command(reactor, args, stdout=None, stderr=None, client=None, agent=None):
    """
    Run a magic-folder command with the given args

    :param IWebSocketClientAgent agent: override the WebSocket connection agent

    :returns: a Deferred which fires with the result of doing this
        magic-folder (sub)command.
    """
    options = MagicFolderCommand()
    options.reactor = reactor
    if stdout is not None:
        options.stdout = stdout
    if stderr is not None:
        options.stderr = stderr
    if client is not None:
        options._client = client
    if agent is not None:
        options._ws_agent = agent

    try:
        options.parseOptions(args)
        maybe_fail_experimental_command(options)
    except usage.UsageError as e:
        print("Error: {}".format(e))
        # if a user just typed "magic-folder" don't make them re-run
        # with "--help" just to see the sub-commands they were
        # supposed to use
        if len(sys.argv) == 1:
            print(options)
        raise SystemExit(1)

    return run_magic_folder_options(options)


# maps str -> str
# "subcommand" -> "experimental feature"
# where the experimental feature must exist in
# magic_folder.config._features
_subcommand_to_experimental_features = {
    "invite": "invites",
    "join": "invites",
}

def maybe_fail_experimental_command(options):
    """
    Attempt to produce an error early if the user used an experimental
    feature that is not enabled. We could fail to find a configuration
    at all, which means we don't know what commands are enabled or
    not, so we let it through in that case.
    """
    exp_sub = _subcommand_to_experimental_features.get(options.subCommand, None)
    if exp_sub:
        if not options.config.feature_enabled(exp_sub):
            try:
                maybe_config_option = " --config {}".format(options._config_path.path)
            except AttributeError:
                maybe_config_option = ""
            raise usage.UsageError(
                '"magic-folder {}" depends on experimental feature "{}"'
                ' which is not enabled.\nUse "magic-folder{} set-config'
                ' --enable {}" to enable it.'.format(
                    options.subCommand,
                    exp_sub,
                    maybe_config_option,
                    exp_sub,
                )
            )


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
        return dispatch_magic_folder_command(reactor, sys.argv[1:])
    return react(main)
