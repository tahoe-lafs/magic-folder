from __future__ import print_function

import os
import sys
from six.moves import (
    StringIO as MixedIO,
)
from types import NoneType
from ConfigParser import SafeConfigParser
import json
from collections import (
    defaultdict,
)

from appdirs import (
    user_config_dir,
)

from zope.interface import (
    implementer,
)

from hyperlink import (
    DecodedURL,
)

import importlib_metadata

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
from twisted.python.failure import (
    Failure,
)
from twisted.application.service import (
    Service,
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
from allmydata.util.assertutil import precondition
from allmydata.util import (
    base32,
)
from allmydata.util.encodingutil import (
    argv_to_abspath,
    argv_to_unicode,
    to_str,
    quote_local_unicode_path,
)
from allmydata.util import fileutil
from allmydata.util.encodingutil import quote_output

from allmydata.scripts.common import (
    get_aliases,
)
from allmydata.client import (
    read_config,
)

from .magic_folder import (
    MagicFolder,
    load_magic_folders,
    save_magic_folders,
)
from .web import (
    magic_folder_web_service,
)

from .invite import (
    magic_folder_invite as _invite
)

from .list import (
    magic_folder_list
)

from .create import (
    magic_folder_create as _create
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

from .join import (
    magic_folder_join as _join
)

from ._coverage import (
    coverage_service,
)

from .util.observer import ListenObserver


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
        ("config", "c", None,
         "A non-existant directory to contain config (default {})".format(_default_config_path)),
        ("listen-endpoint", "l", None, "A Twisted server string for our REST API (e.g. \"tcp:4321\")"),
        ("node-directory", "n", None, "The local path to our Tahoe-LAFS client's directory"),
    ]
    description = (
        "Initialize a new magic-folder daemon. A single daemon may run "
        "any number of magic-folders (use \"magic-folder create\" to "
        "create a new one."
    )

    def postOptions(self):
        # defaults
        if self['config'] is None:
            self['config'] = _default_config_path

        # required args
        if self['listen-endpoint'] is None:
            raise usage.UsageError("--listen-endpoint / -l is required")
        if self['node-directory'] is None:
            raise usage.UsageError("--node-directory / -n is required")

        # validate
        if FilePath(self['config']).exists():
            raise usage.UsageError("Directory '{}' already exists".format(self['config']))


@inlineCallbacks
def initialize(options):

    try:
        rc = yield magic_folder_initialize(
            FilePath(options['config']),
            options['listen-endpoint'],
            FilePath(options['node-directory']),
        )
        print("Created Magic Folder daemon configuration in:\n     {}".format(options['config']))
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
        ("config", "c", None,
         "A non-existant directory to contain config (default {})".format(_default_config_path)),
        ("listen-endpoint", "l", None, "A Twisted server string for our REST API (e.g. \"tcp:4321\")"),
        ("node-directory", "n", None, "A local path which is a Tahoe-LAFS node-directory"),
        ("author-name", "a", None, "The name for the author to use in each migrated magic-folder"),
    ]
    synopsis = (
        "\n\nCreate a new magic-folder daemon configuration in the --config "
        "path, using values from the --node-directory Tahoe-LAFS node."
    )

    def postOptions(self):
        # defaults
        if self['config'] is None:
            self['config'] = _default_config_path

        # required args
        if self['listen-endpoint'] is None:
            raise usage.UsageError("--listen-endpoint / -l is required")
        if self['node-directory'] is None:
            raise usage.UsageError("--node-directory / -n is required")
        if self['author-name'] is None:
            raise usage.UsageError("--author-name / -a is required")

        # validate
        if FilePath(self['config']).exists():
            raise usage.UsageError("Directory '{}' already exists".format(self['config']))
        if not FilePath(self['node-directory']).exists():
            raise usage.UsageError("--node-directory '{}' doesn't exist".format(self['node-directory']))
        if not FilePath(self['node-directory']).child("tahoe.cfg").exists():
            raise usage.UsageError(
                "'{}' doesn't look like a Tahoe node-directory (no tahoe.cfg)".format(self['node-directory'])
            )


@inlineCallbacks
def migrate(options):

    try:
        config = yield magic_folder_migrate(
            FilePath(options['config']),
            options['listen-endpoint'],
            FilePath(options['node-directory']),
            options['author-name'],
        )
        print("Created Magic Folder daemon configuration in:\n     {}".format(options['config']))
        print("\nIt contains the following magic-folders:")
        for name in config.list_magic_folders():
            mf = config.get_magic_folder(name)
            print("  {}: author={}".format(name, mf.author.name))

    except Exception as e:
        print("%s" % str(e), file=options.stderr)
        returnValue(1)

    returnValue(0)


class CreateOptions(usage.Options):
    nickname = None  # NOTE: *not* the "name of this magic-folder"
    local_dir = None
    synopsis = "MAGIC_ALIAS: [NICKNAME LOCAL_DIR]"
    optParameters = [
        ("poll-interval", "p", "60", "How often to ask for updates"),
        ("name", "n", "default", "The name of this magic-folder"),
    ]
    description = (
        "Create a new magic-folder. If you specify NICKNAME and "
        "LOCAL_DIR, this client will also be invited and join "
        "using the given nickname. A new alias (see 'tahoe list-aliases') "
        "will be added with the master folder's writecap."
    )

    def parseArgs(self, alias, nickname=None, local_dir=None):
        super(CreateOptions, self).parseArgs()
        alias = argv_to_unicode(alias)
        if not alias.endswith(u':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = None if nickname is None else argv_to_unicode(nickname)
        try:
            if int(self['poll-interval']) <= 0:
                raise ValueError("should be positive")
        except ValueError:
            raise usage.UsageError(
                "--poll-interval must be a positive integer"
            )

        # Expand the path relative to the current directory of the CLI command, not the node.
        self.local_dir = None if local_dir is None else argv_to_abspath(local_dir, long_path=False)

        if self.nickname and not self.local_dir:
            raise usage.UsageError("If NICKNAME is specified then LOCAL_DIR must also be specified.")


@inlineCallbacks
def create(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, (unicode, NoneType)), nickname=options.nickname)
    precondition(isinstance(options.local_dir, (unicode, NoneType)), local_dir=options.local_dir)

    try:
        from twisted.internet import reactor
        treq = HTTPClient(Agent(reactor))

        name = options['name']
        nodedir = options.parent.node_directory
        localdir = options.local_dir
        rc = yield _create(options.alias, options.nickname, name, nodedir, localdir, options["poll-interval"], treq)
        print("Alias %s created" % (quote_output(options.alias),), file=options.stdout)
    except Exception as e:
        print("%s" % str(e), file=options.stderr)
        returnValue(1)

    returnValue(rc)

class ListOptions(usage.Options):
    description = (
        "List all magic-folders this client has joined"
    )
    optFlags = [
        ("json", "", "Produce JSON output")
    ]



@inlineCallbacks
def list_(options):
    try:
        response = yield magic_folder_list(options.parent.node_directory)
        print("response:{}".format(response))
    except Exception as e:
        print("%s" % str(e), file=options.stderr)
        returnValue(1)

    # return 0

    # folders = load_magic_folders(options.parent.node_directory)
    # if options["json"]:
    #     _list_json(options, folders)
    #     return 0
    # _list_human(options, folders)
    # return 0

    returnValue(0)


def _list_json(options, folders):
    """
    List our magic-folders using JSON
    """
    info = dict()
    for name, details in folders.items():
        info[name] = {
            u"directory": details["directory"],
        }
    print(json.dumps(info), file=options.stdout)
    return 0


def _list_human(options, folders):
    """
    List our magic-folders for a human user
    """
    if folders:
        print("This client has the following magic-folders:", file=options.stdout)
        biggest = max([len(nm) for nm in folders.keys()])
        fmt = "  {:>%d}: {}" % (biggest, )
        for name, details in folders.items():
            print(fmt.format(name, details["directory"]), file=options.stdout)
    else:
        print("No magic-folders", file=options.stdout)


class InviteOptions(usage.Options):
    nickname = None
    synopsis = "MAGIC_ALIAS: NICKNAME"
    stdin = MixedIO(u"")
    optParameters = [
        ("name", "n", "default", "The name of this magic-folder"),
    ]
    description = (
        "Invite a new participant to a given magic-folder. The resulting "
        "invite-code that is printed is secret information and MUST be "
        "transmitted securely to the invitee."
    )

    def parseArgs(self, alias, nickname=None):
        super(InviteOptions, self).parseArgs()
        alias = argv_to_unicode(alias)
        if not alias.endswith(u':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = argv_to_unicode(nickname)
        aliases = get_aliases(self.parent.node_directory)
        self.aliases = aliases

@inlineCallbacks
def invite(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, unicode), nickname=options.nickname)

    from twisted.internet import reactor
    treq = HTTPClient(Agent(reactor))

    try:
        invite_code = yield _invite(options.parent.node_directory, options.alias, options.nickname, treq)
        print("{}".format(invite_code), file=options.stdout)
    except Exception as e:
        print("magic-folder: {}".format(str(e)))
        returnValue(1)

    returnValue(0)

class JoinOptions(usage.Options):
    synopsis = "INVITE_CODE LOCAL_DIR"
    dmd_write_cap = ""
    magic_readonly_cap = ""
    optParameters = [
        ("poll-interval", "p", "60", "How often to ask for updates"),
        ("name", "n", "default", "Name of the magic-folder"),
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
        # Expand the path relative to the current directory of the CLI command, not the node.
        self.local_dir = None if local_dir is None else argv_to_abspath(local_dir, long_path=False)
        self.invite_code = to_str(argv_to_unicode(invite_code))
        if self['author'] is None:
            self['author'] = os.environ.get('USERNAME', os.environ.get('USER', None))
            if self['author'] is None:
                raise usage.UsageError(
                    "--author not provided and no USERNAME environment-variable"
                )


def join(options):
    """
    ``magic-folder join`` entrypoint.
    """
    try:
        invite_code = options.invite_code
        node_directory = options.parent.node_directory
        local_directory = options.local_dir
        name = options["name"]
        poll_interval = options["poll-interval"]
        author = options["author"]

        rc = _join(invite_code, node_directory, local_directory, name, poll_interval, author)
    except Exception as e:
        print(e, file=options.stderr)
        return 1

    return rc

class LeaveOptions(usage.Options):
    description = "Remove a magic-folder and forget all state"
    optParameters = [
        ("name", "n", "default", "Name of magic-folder to leave"),
    ]


def _leave(node_directory, name, existing_folders):
    privdir = os.path.join(node_directory, u"private")
    db_fname = os.path.join(privdir, u"magicfolder_{}.sqlite".format(name))

    # delete from YAML file and re-write it
    del existing_folders[name]
    save_magic_folders(node_directory, existing_folders)

    # delete the database file
    try:
        fileutil.remove(db_fname)
    except Exception as e:
        raise Exception("unable to remove %s due to %s: %s"
                        % (quote_local_unicode_path(db_fname),
                           e.__class__.__name__, str(e)))

    # if this was the last magic-folder, disable them entirely
    if not existing_folders:
        parser = SafeConfigParser()
        parser.read(os.path.join(node_directory, u"tahoe.cfg"))
        parser.remove_section("magic_folder")
        with open(os.path.join(node_directory, u"tahoe.cfg"), "w") as f:
            parser.write(f)

    return 0

def leave(options):
    existing_folders = load_magic_folders(options.parent.node_directory)

    if not existing_folders:
        print("No magic-folders at all", file=options.stderr)
        return 1

    if options["name"] not in existing_folders:
        print("No such magic-folder '{}'".format(options["name"]), file=options.stderr)
        return 1

    try:
        _leave(options.parent.node_directory, options["name"], existing_folders)
    except Exception as e:
        print("Warning: {}".format(str(e)))
        return 1

    return 0


class RunOptions(usage.Options):
    optParameters = [
        ("web-port", None, None,
         "String description of an endpoint on which to run the web interface (required).",
        ),
    ]

    def postOptions(self):
        if self['web-port'] is None:
            raise usage.UsageError("Must specify a listening endpoint with --web-port")


def main(options):
    """
    This is the long-running magic-folders function which performs
    synchronization between local and remote folders.
    """
    from twisted.internet import  reactor
    service = MagicFolderService.from_node_directory(
        reactor,
        options.parent.node_directory,
        options["web-port"],
    )
    return service.run()


@inline_callbacks
def poll(label, operation, reactor):
    while True:
        print("Polling {}...".format(label))
        if (yield operation()):
            print("Positive result ({}), done.".format(label))
            break
        print("Negative result ({}), sleeping...".format(label))
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
    webport = attr.ib()
    tahoe_nodedir = attr.ib()
    _state = attr.ib(
        validator=attr.validators.instance_of(MagicFolderServiceState),
        default=attr.Factory(MagicFolderServiceState),
    )

    def __attrs_post_init__(self):
        MultiService.__init__(self)
        self.tahoe_client = TahoeClient(
            DecodedURL.from_text(
                self.config.get_config_from_file(b"node.url").decode("utf-8"),
            ),
            Agent(self.reactor),
        )
        web_endpoint = RecordLocation(
            serverFromString(self.reactor, self.webport),
            self._write_web_url,
        )
        self._listen_endpoint = ListenObserver(web_endpoint)
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
        return self.config.get_private_config("api_auth_token")

    @classmethod
    def from_node_directory(cls, reactor, nodedir, webport):
        config = read_config(nodedir, u"client.port")
        return cls(reactor, config, webport, FilePath(nodedir))

    def _when_connected_enough(self):
        # start processing the upload queue when we've connected to
        # enough servers
        k = int(self.config.get_config("client", "shares.needed", 3))
        happy = int(self.config.get_config("client", "shares.happy", 7))
        threshold = min(k, happy + 1)

        @inline_callbacks
        def enough():
            welcome = yield self.tahoe_client.get_welcome()
            if welcome.code != 200:
                returnValue(False)

            welcome_body = json.loads((yield readBody(welcome)))
            if len(welcome_body[u"servers"]) < threshold:
                returnValue(False)
            returnValue(True)
        return poll("connected enough", enough, self.reactor)

    def run(self):
        d = self._when_connected_enough()
        d.addCallback(lambda ignored: self.startService())
        d.addCallback(lambda ignored: self._listen_endpoint.observe())
        d.addCallback(lambda ignored: Deferred())
        return d

    def startService(self):
        MultiService.startService(self)

        magic_folder_configs = load_magic_folders(self.tahoe_nodedir.path)

        ds = []
        for (name, mf_config) in magic_folder_configs.items():
            mf = MagicFolder.from_config(
                self.reactor,
                ClientStandIn(self.tahoe_client, self.config),
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

@attr.s
class ClientStandIn(object):
    nickname = ""
    stats_provider = FakeStats()

    tahoe_client = attr.ib()
    config = attr.ib()
    convergence = attr.ib(default=None)

    def __attrs_post_init__(self):
        if self.convergence is None:
            convergence_s = self.config.get_private_config('convergence')
            self.convergence = base32.a2b(convergence_s)

    def create_node_from_uri(self, uri, rouri=None):
        return Node(self.tahoe_client, from_string(rouri if uri is None else uri))


@implementer(IDirectoryNode)
@attr.s
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

@attr.s
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
        ["quiet", "q", "Operate silently."],
        ["version", "V", "Display version numbers."],
        ["version-and-path", None, "Display version numbers and paths to their locations."],
    ]
    optParameters = [
        ["node-directory", "n", None, NODEDIR_HELP],
    ]

class MagicFolderCommand(BaseOptions):
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    subCommands = [
        ["init", None, InitializeOptions, "Initialize a Magic Folder daemon."],
        ["migrate", None, MigrateOptions, "Migrate a Magic Folder from Tahoe-LAFS 1.14.0 or earlier"],
        ["show-config", None, ShowConfigOptions, "Dump configuration as JSON"],
        ["create", None, CreateOptions, "Create a Magic Folder."],
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
    def node_directory(self):
        if self["node-directory"] is None:
            if self.subCommand in ["init", "migrate", "show-config"]:
                return
            raise usage.UsageError(
                "Must supply --node-directory (or -n)"
            )
        nd = self["node-directory"]
        if not os.path.exists(nd):
            raise usage.UsageError(
                "'{}' does not exist".format(nd)
            )
        if not os.path.isdir(nd):
            raise usage.UsageError(
                "'{}' is not a directory".format(nd)
            )
        if not os.path.exists(os.path.join(nd, "tahoe.cfg")):
            raise usage.UsageError(
                "'{}' doesn't look like a Tahoe directory (no 'tahoe.cfg')".format(nd)
            )
        return nd

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
        # ensure our node-directory is valid
        _ = self.node_directory

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
    "create": create,
    "invite": invite,
    "join": join,
    "leave": leave,
    "list": list_,
    "run": main,
}


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
        return maybeDeferred(f, so)
    except Exception as e:
        print(u"Error: {}".format(e), file=options.stderr)
        if options['debug']:
            raise


class _MagicFolderService(Service):
    def __init__(self, options):
        self.options = options

    def startService(self):
        d = maybeDeferred(do_magic_folder, self.options)
        d.addBoth(_stop)

def _stop(reason):
    if isinstance(reason, Failure):
        print(reason.getTraceback())
    from twisted.internet import reactor
    reactor.callWhenRunning(reactor.stop)

# Provide the option parsing helper for the IServiceMaker plugin that lets us
# have "twist magic_folder ...".
Options = MagicFolderCommand

# Provide the IService-building helper for the IServiceMaker plugin.
def makeService(options):
    """
    :param MagicFolderCommand options: The parsed options for the comand
        invocation.

    :return IService: An object providing ``IService`` which performs the
        magic-folder operation requested by ``options`` when it is started.
    """
    service = MultiService()
    _MagicFolderService(options).setServiceParent(service)
    if options["coverage"]:
        coverage_service().setServiceParent(service)
    return service


def main(reactor):
    options = MagicFolderCommand()
    options.parseOptions(sys.argv[1:])
    return do_magic_folder(options)

def run():
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

    if options.subCommand in ["init", "migrate", "show-config"]:
        return react(main)

    # run the same way as originally ported for "other" commands
    console_scripts = importlib_metadata.entry_points()["console_scripts"]
    magic_folder = list(
        script
        for script
        in console_scripts
        if script.name == "twist"
    )[0]
    argv = ["twist", "--log-level=debug", "--log-format=text", "magic_folder"] + sys.argv[1:]
    magic_folder.load()(argv)
