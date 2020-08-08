from __future__ import print_function

import sys
import getpass
import traceback
from six.moves import (
    StringIO as MixedIO,
)
import json
from collections import (
    defaultdict,
)

from nacl.encoding import (
    Base32Encoder,
)

from appdirs import (
    user_config_dir,
)

from zope.interface import (
    implementer,
)

import attr

from io import (
    BytesIO,
)
from eliot import (
    start_action,
    log_call,
)
from eliot.twisted import (
    DeferredContext,
)

from twisted.internet.interfaces import (
    IStreamServerEndpoint,
)
from twisted.internet.endpoints import (
    serverFromString,
)
from twisted.internet.task import (
    react,
)

from twisted.web.client import (
    Agent,
    readBody,
    FileBodyProducer,
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

from allmydata.interfaces import (
    IDirectoryNode,
    IURI,
)
from allmydata.uri import (
    from_string,
)
from allmydata.util.encodingutil import (
    argv_to_unicode,
    to_str,
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

from .invite import (
    magic_folder_invite
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

from .join import (
    magic_folder_join
)


_default_config_path = user_config_dir("magic-folder")


class ShowConfigOptions(usage.Options):
    """
    Dump current configuration as JSON.
    """

    optParameters = [
        ("config", "c", None, "An existing config directory (default {}".format(_default_config_path)),
    ]
    description = (
        "Dump magic-folder configuration as JSON"
    )

    def postOptions(self):
        # defaults
        if self['config'] is None:
            self['config'] = _default_config_path

        # validate
        if not FilePath(self['config']).exists():
            raise usage.UsageError("Directory '{}' doesn't exist".format(self['config']))


@inlineCallbacks
def show_config(options):

    try:
        rc = yield magic_folder_show_config(
            FilePath(options['config']),
        )
    except Exception as e:
        print("%s" % str(e), file=options.stderr)
        returnValue(1)

    returnValue(rc)


class InitializeOptions(usage.Options):
    """
    Create and initialize a new Magic Folder daemon directory (which
    will have no magic-folders in it; use "magic-folder create" for
    that).
    """

    optParameters = [
        ("listen-endpoint", "l", None, "A Twisted server string for our REST API (e.g. \"tcp:4321\")"),
        ("node-directory", "n", None, "The local path to our Tahoe-LAFS client's directory"),
    ]
    description = (
        "Initialize a new magic-folder daemon. A single daemon may run "
        "any number of magic-folders (use \"magic-folder create\" to "
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

    try:
        rc = yield magic_folder_initialize(
            options.parent._config_path,
            options['listen-endpoint'],
            FilePath(options['node-directory']),
        )
        print(
            "Created Magic Folder daemon configuration in:\n     {}".format(options.parent._config_path.path),
            file=options.stdout,
        )
    except Exception as e:
        print("%s" % str(e), file=options.stderr)
        returnValue(1)

    returnValue(rc)


class MigrateOptions(usage.Options):
    """
    Migrate a magic-folder configuration from an existing Tahoe-LAFS
    node-directory.
    """

    optParameters = [
        ("listen-endpoint", "l", None, "A Twisted server string for our REST API (e.g. \"tcp:4321\")"),
        ("node-directory", "n", None, "A local path which is a Tahoe-LAFS node-directory"),
        ("author", "A", None, "The name for the author to use in each migrated magic-folder"),
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

    try:
        config = yield magic_folder_migrate(
            options.parent._config_path,
            options['listen-endpoint'],
            FilePath(options['node-directory']),
            options['author'],
        )
        print(
            "Created Magic Folder daemon configuration in:\n     {}".format(options.parent._config_path.path),
            file=options.stdout,
        )
        print("\nIt contains the following magic-folders:", file=options.stdout)
        for name in config.list_magic_folders():
            mf = config.get_magic_folder(name)
            print("  {}: author={}".format(name, mf.author.name), file=options.stdout)

    except Exception as e:
        print("%s" % str(e), file=options.stderr)
        returnValue(1)

    returnValue(0)


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
    yield magic_folder_create(
        options.parent.config,
        argv_to_unicode(options["name"]),
        argv_to_unicode(options["author"]),
        options.local_dir,
        options["poll-interval"],
        treq,
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


def list_(options):
    """
    List existing magic-folders.
    """
    mf_info = _magic_folder_info(options)
    if options["json"]:
        print(json.dumps(mf_info, indent=4), file=options.stdout)
        return
    return _list_human(mf_info, options.stdout, options["include-secret-information"])


def _magic_folder_info(options):
    """
    Get information about all magic-folders

    :returns: JSON-able dict
    """
    info = dict()
    config = options.parent.config
    for name in config.list_magic_folders():
        mf = config.get_magic_folder(name)
        info[name] = {
            u"author": {
                u"name": mf.author.name,
                u"verify_key": mf.author.verify_key.encode(Base32Encoder),
            },
            u"stash_path": mf.stash_path.path,
            u"magic_path": mf.magic_path.path,
            u"poll_interval": mf.poll_interval,
            u"is_admin": mf.is_admin(),
        }
        if options['include-secret-information']:
            info[name][u"author"][u"signing_key"] = mf.author.signing_key.encode(Base32Encoder)
            info[name][u"collective_dircap"] = mf.collective_dircap.encode("ascii")
            info[name][u"upload_dircap"] = mf.upload_dircap.encode("ascii")
    return info


def _list_human(info, stdout, include_secrets):
    """
    List our magic-folders for a human user
    """
    if include_secrets:
        template = (
            "    location: {magic_path}\n"
            "   stash-dir: {stash_path}\n"
            "      author: {author[name]} (private_key: {author[signing_key]})\n"
            "  collective: {collective_dircap}\n"
            "    personal: {upload_dircap}\n"
            "     updates: every {poll_interval}s\n"
            "       admin: {is_admin}\n"
        )
    else:
        template = (
            "    location: {magic_path}\n"
            "   stash-dir: {stash_path}\n"
            "      author: {author[name]} (public_key: {author[verify_key]})\n"
            "     updates: every {poll_interval}s\n"
            "       admin: {is_admin}\n"
        )

    if info:
        print("This client has the following magic-folders:", file=stdout)
        for name, details in info.items():
            print("{}:".format(name), file=stdout)
            print(template.format(**details).rstrip("\n"), file=stdout)
    else:
        print("No magic-folders", file=stdout)


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
        self.invite_code = to_str(argv_to_unicode(invite_code))

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
    # XXX start logging
    from twisted.internet import  reactor
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
@implementer(IStreamServerEndpoint)
class RecordLocation(object):
    """
    An endpoint wrapper which supports an observer which gets called with the
    address of the listening port whenever one is created.
    """
    _endpoint = attr.ib()
    _recorder = attr.ib()

    @inlineCallbacks
    def listen(self, protocolFactory):
        port = yield self._endpoint.listen(protocolFactory)
        self._recorder(port.getHost())
        returnValue(port)


@attr.s
class MagicFolderServiceState(object):
    """
    Represent the operational state for a group of Magic Folders.

    This is intended to be easy to instantiate.  It was split off
    ``MagicFolderService`` specifically to make testing easier.

    :ivar {unicode: (dict, magic_folder.magic_folder.MagicFolder)} _folders:
        The configuration and services for configured magic folders.
    """
    _folders = attr.ib(default=attr.Factory(dict))

    def get_magic_folder(self, name):
        """
        Get the Magic Folder with the given name.

        :param unicode name: The name of the Magic Folder.

        :raise KeyError: If there is no Magic Folder by that name.

        :return: The ``MagicFolder`` instance corresponding to the given name.
        """
        config, service = self._folders[name]
        return service


    def add_magic_folder(self, name, config, service):
        """
        Track a new Magic Folder.

        :param unicode name: The name of the new Magic Folder.

        :param dict config: The new Magic Folder's configuration.

        :param service: The ``MagicFolder`` instance representing the new
            Magic Folder.
        """
        if name in self._folders:
            raise ValueError("Already have a Magic Folder named {!r}".format(name))
        self._folders[name] = (config, service)


    def iter_magic_folder_configs(self):
        """
        Iterate over all of the Magic Folder names and configurations.

        :return: An iterator of two-tuples of a unicode name and a Magic
            Folder configuration.
        """
        for (name, (config, service)) in self._folders.items():
            yield (name, config)


@attr.s
class MagicFolderService(MultiService):
    """
    :ivar FilePath tahoe_nodedir: The filesystem path to the Tahoe-LAFS node
        with which to interact.

    :ivar MagicFolderServiceState _state: The Magic Folder state in use by
        this service.
    """
    reactor = attr.ib()
    config = attr.ib()
    _state = attr.ib(
        validator=attr.validators.instance_of(MagicFolderServiceState),
        default=attr.Factory(MagicFolderServiceState),
    )

    def __attrs_post_init__(self):
        MultiService.__init__(self)
        self.tahoe_client = TahoeClient(
            self.config.tahoe_client_url,
            Agent(self.reactor),
        )
        self._listen_endpoint = serverFromString(
            self.reactor,
            self.config.api_endpoint,
        )
        web_service = magic_folder_web_service(
            self._listen_endpoint,
            self._state,
            self._get_auth_token,
        )
        web_service.setServiceParent(self)

    def _write_web_url(self, host):
        """
        Write a state file to the Tahoe-LAFS node directory containing a URL
        pointing to our web server listening at the given address.

        :param twisted.internet.address.IPv4Address host: The address where
            our web server is listening.
        """
        self.config.write_config_file(
            u"magic-folder.url",
            "http://{}:{}/".format(host.host, host.port),
        )

    def _get_auth_token(self):
        return self.config.api_token

    @classmethod
    def from_config(cls, reactor, config):
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
            welcome = yield self.tahoe_client.get_welcome()
            if welcome.code != 200:
                returnValue((False, "Failed to get welcome page"))

            welcome_body = json.loads((yield readBody(welcome)))
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
#        d.addCallback(lambda ignored: self._listen_endpoint.observe())
        d.addCallback(lambda ignored: Deferred())
        return d

    def startService(self):
        MultiService.startService(self)

        ds = []
        for name in self.config.list_magic_folders():
            mf_config = self.config.get_magic_folder(name)
            mf = MagicFolder.from_config(
                self.reactor,
                self.tahoe_client,
                name,
                mf_config,
                self.config,
            )
            self._state.add_magic_folder(name, mf_config, mf)
            mf.setServiceParent(self)
            ds.append(mf.ready())
        # The integration tests look for this message.  You cannot get rid of
        # it.
        print("Completed initial Magic Folder setup")
        self._starting = gatherResults(ds)

    def stopService(self):
        self._starting.cancel()
        MultiService.stopService(self)
        return self._starting


@attr.s
class FakeStats(object):
    counters = attr.ib(default=attr.Factory(lambda: defaultdict(int)))

    def count(self, ctr, delta):
        pass


@implementer(IDirectoryNode)
@attr.s(frozen=True)
class Node(object):
    tahoe_client = attr.ib()
    uri = attr.ib()

    def __attrs_post_init__(self):
        if not IURI.providedBy(self.uri):
            raise TypeError("{} does not provide IURI".format(self.uri))

    def is_unknown(self):
        return False

    def is_readonly(self):
        return self.uri.is_readonly()

    @log_call
    def get_uri(self):
        return self.uri.to_string()

    @log_call
    def get_size(self):
        return self.uri.get_size()

    def get_readonly_uri(self):
        return self.uri.get_readonly().to_string()

    def list(self):
        return self.tahoe_client.list_directory(self.uri)

    def download_best_version(self, progress):
        return self.tahoe_client.download_best_version(
            self.uri, progress
        )

    def add_file(self, name, uploadable, metadata=None, overwrite=True, progress=None):
        action = start_action(
            action_type=u"magic-folder:cli:add_file",
            name=name,
        )
        with action.context():
            d = DeferredContext(
                self.tahoe_client.add_file(
                    self.uri, name, uploadable, metadata, overwrite, progress,
                ),
            )
            return d.addActionFinish()

@attr.s(frozen=True)
class TahoeClient(object):
    node_uri = attr.ib()
    agent = attr.ib()

    def get_welcome(self):
        return self.agent.request(
            b"GET",
            self.node_uri.add(u"t", u"json").to_uri().to_text().encode("ascii"),
        )

    @inline_callbacks
    def list_directory(self, uri):
        api_uri = self.node_uri.child(
                u"uri",
                uri.to_string().decode("ascii"),
            ).add(
                u"t",
                u"json",
            ).to_uri().to_text().encode("ascii")
        action = start_action(
            action_type=u"magic-folder:cli:list-dir",
            filenode_uri=uri.to_string().decode("ascii"),
            api_uri=api_uri,
        )
        with action.context():
            response = yield self.agent.request(
                b"GET",
                api_uri,
            )
            if response.code != 200:
                raise Exception("Error response from list endpoint: {}".format(response))

            kind, dirinfo = json.loads((yield readBody(response)))
            if kind != u"dirnode":
                raise ValueError("Object is a {}, not a directory".format(kind))

            action.add_success_fields(
                children=dirinfo[u"children"],
            )

        returnValue({
            name: (
                Node(
                    self,
                    from_string(
                        json_metadata.get("rw_uri", json_metadata["ro_uri"]).encode("ascii"),
                    ),
                ),
                json_metadata[u"metadata"],
            )
            for (name, (child_kind, json_metadata))
            in dirinfo[u"children"].items()
        })

    @inline_callbacks
    def download_best_version(self, filenode_uri, progress):
        uri = self.node_uri.child(
            u"uri",
            filenode_uri.to_string().decode("ascii"),
        ).to_uri().to_text().encode("ascii")

        with start_action(action_type=u"magic-folder:cli:download", uri=uri):
            response = yield self.agent.request(
                b"GET",
                uri,
            )
            if response.code != 200:
                raise Exception(
                    "Error response from download endpoint: {code} {phrase}".format(
                        **vars(response)
                    ))

        returnValue((yield readBody(response)))

    @inline_callbacks
    def add_file(self, dirnode_uri, name, uploadable, metadata, overwrite, progress):
        size = yield uploadable.get_size()
        contents = b"".join((yield uploadable.read(size)))

        uri = self.node_uri.child(
            u"uri",
        ).to_uri().to_text().encode("ascii")
        action = start_action(
            action_type=u"magic-folder:cli:add_file:put",
            uri=uri,
            size=size,
        )
        with action:
            upload_response = yield self.agent.request(
                b"PUT",
                uri,
                bodyProducer=FileBodyProducer(BytesIO(contents)),
            )

            if upload_response.code != 200:
                raise Exception(
                    "Error response from upload endpoint: {code} {phrase}".format(
                        **vars(upload_response)
                    ),
                )

            filecap = yield readBody(upload_response)

        uri = self.node_uri.child(
            u"uri",
            dirnode_uri.to_string().decode("ascii"),
            u"",
        ).add(
            u"t",
            u"set-children",
        ).add(
            u"overwrite",
            u"true" if overwrite else u"false",
        ).to_uri().to_text().encode("ascii")
        action = start_action(
            action_type=u"magic-folder:cli:add_file:metadata",
            uri=uri,
        )
        with action:
            response = yield self.agent.request(
                b"POST",
                uri,
                bodyProducer=FileBodyProducer(
                    BytesIO(
                        json.dumps({
                            name: [
                                u"filenode", {
                                    "ro_uri": filecap,
                                    "size": size,
                                    "metadata": metadata,
                                },
                            ],
                        }).encode("utf-8"),
                    ),
                ),
            )
            if response.code != 200:
                raise Exception("Error response from metadata endpoint: {code} {phrase}".format(
                    **vars(response)
                ))
        returnValue(Node(self, from_string(filecap)))



NODEDIR_HELP = (
    "Specify which Tahoe node directory should be used. The "
    "directory should contain a full Tahoe node."
)


class BaseOptions(usage.Options):
    optFlags = [
        ["version", "V", "Display version numbers."],
    ]
    optParameters = [
        ("config", "c", _default_config_path,
         "The directory containing config (default {})".format(_default_config_path)),
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
        ["run", None, RunOptions, "Run the Magic Folders synchronization process."],
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
            "Please run e.g. 'magic-folder create --help' for more "
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
def do_magic_folder(options):
    """
    :returns: a Deferred which fires with the result of doig this
        magic-folder subcommand.
    """
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    try:
        yield maybeDeferred(f, so)
    except Exception as e:
        print(u"Error: {}".format(e), file=options.stderr)
        if options['debug']:
            traceback.print_exc(file=options.stderr)
        raise SystemExit(1)

@inlineCallbacks
def main(reactor):
    options = MagicFolderCommand()
    options.parseOptions(sys.argv[1:])
    yield do_magic_folder(options)


def _entry():
    """
    Implement the *magic-folder* console script declared in ``setup.py``.

    :return: ``None``
    """
    from eliot import to_file
    from os import getpid
    to_file(open("magic-folder-cli.{}.eliot".format(getpid()), "w"))

    # for most commands that produce output for users we don't want
    # the logging etc that 'twist' does .. only doing this for "new"
    # commands for now

    options = MagicFolderCommand()
    try:
        options.parseOptions(sys.argv[1:])
    except usage.UsageError as e:
        print("Error: {}".format(e))
        # if a user just typed "magic-folder" don't make them re-run
        # with "--help" just to see the sub-commands they were
        # supposed to use
        if len(sys.argv) == 1:
            print(options)
        return 1

    return react(main)
