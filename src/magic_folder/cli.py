from __future__ import print_function

import os
import sys
import traceback
from six.moves import (
    StringIO as MixedIO,
)
from types import NoneType
from datetime import datetime
from ConfigParser import SafeConfigParser
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
from allmydata.util.abbreviate import abbreviate_space, abbreviate_time
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

from .status import (
    status as _status,
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
        print(
            "Created Magic Folder daemon configuration in:\n     {}".format(options['config']),
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
        print(
            "Created Magic Folder daemon configuration in:\n     {}".format(options['config']),
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
        ("name", "n", "default", "The name of this magic-folder"),
        ("author-name", "a", None, "Our name for Snapshots authored here"),
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
        if self['author-name'] is None:
            raise usage.UsageError(
                "Must supply --author-name / -a"
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
        options["name"],
        options["author-name"],
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
            },
            u"stash_path": mf.stash_path.path,
            u"magic_path": mf.magic_path.path,
            u"poll_interval": mf.poll_interval,
        }
        if options['include-secret-information']:
            info[name][u"author"][u"verify_key"] = mf.author.verify_key.encode(Base32Encoder)
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
            "      author: {author[name]} ({author[verify_key]})\n"
            "  collective: {collective_dircap}\n"
            "    personal: {upload_dircap}\n"
            "     updates: every {poll_interval}s\n"
        )
    else:
        template = (
            "    location: {magic_path}\n"
            "   stash-dir: {stash_path}\n"
            "      author: {author[name]}\n"
            "     updates: every {poll_interval}s\n"
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
        ("name", "n", "default", "Name of an existing magic-folder"),
    ]
    description = (
        "Invite a new participant to a given magic-folder. The resulting "
        "invite-code that is printed is secret information and MUST be "
        "transmitted securely to the invitee."
    )

    def parseArgs(self, nickname):
        super(InviteOptions, self).parseArgs()
        self.nickname = argv_to_unicode(nickname)


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
        ("name", "n", "default", "Name for the new magic-folder"),
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
    return magic_folder_join(
        options.parent.config,
        options.invite_code,
        options.local_dir,
        options["name"],
        options["poll-interval"],
        options["author"],
    )


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
        print("Warning: {}".format(str(e)), file=options.stderr)
        return 1

    return 0


class StatusOptions(usage.Options):
    synopsis = ""
    stdin = MixedIO(u"")
    optParameters = [
        ("name", "n", "default", "Name for the magic-folder to show status"),
    ]


def _item_status(item, now, longest):
    paddedname = (' ' * (longest - len(item['path']))) + item['path']
    if 'failure_at' in item:
        ts = datetime.fromtimestamp(item['started_at'])
        prog = 'Failed %s (%s)' % (abbreviate_time(now - ts), ts)
    elif item['percent_done'] < 100.0:
        if 'started_at' not in item:
            prog = 'not yet started'
        else:
            so_far = now - datetime.fromtimestamp(item['started_at'])
            if so_far.seconds > 0.0:
                rate = item['percent_done'] / so_far.seconds
                if rate != 0:
                    time_left = (100.0 - item['percent_done']) / rate
                    prog = '%2.1f%% done, around %s left' % (
                        item['percent_done'],
                        abbreviate_time(time_left),
                    )
                else:
                    time_left = None
                    prog = '%2.1f%% done' % (item['percent_done'],)
            else:
                prog = 'just started'
    else:
        prog = ''
        for verb in ['finished', 'started', 'queued']:
            keyname = verb + '_at'
            if keyname in item:
                when = datetime.fromtimestamp(item[keyname])
                prog = '%s %s' % (verb, abbreviate_time(now - when))
                break

    return "  %s: %s" % (paddedname, prog)


@inline_callbacks
def status(options):
    """
    ``magic-folder status`` entry-point.

    :param StatusOptions options: Values for configurable status parameters.

    :return Deferred: A ``Deferred`` which fires with an exit status for the
        process when the status operation has completed.
    """
    nodedir = options.parent.node_directory
    stdout, stderr = options.stdout, options.stderr

    # Create a client without persistent connections to simplify testing.
    # Connections will typically be to localhost anyway so there isn't
    # much performance difference.
    from twisted.internet import reactor
    treq = HTTPClient(Agent(reactor))

    name = options["name"].decode("utf-8")
    try:
        status_obj = yield _status(
            name,
            FilePath(nodedir),
            treq,
        )
    except Exception as e:
        print(e, file=stderr)
        returnValue(1)
    else:
        print(_format_status(datetime.now(), status_obj), file=stdout)
        returnValue(0)


def _format_status(now, status_obj):
    """
    Format a ``Status`` as a unicode string.

    :param datetime now: A time to use as current.

    :param Status status_obj: The object to use to fill the string with
        details.

    :return unicode: Text roughly describing ``status_obj`` to a person.
    """
    return u"""
Magic-folder status for '{folder_name}':

Local files:
{local_files}

Remote files:
{remote_files}

{magic_folder_status}
""".format(
    folder_name=status_obj.folder_name,
    local_files=u"\n".join(list(
        _format_local_files(now, status_obj.local_files)
    )),
    remote_files=u"\n".join(list(
        _format_remote_files(now, status_obj.remote_files)
    )),
    magic_folder_status=u"\n".join(list(
        _format_magic_folder_status(now, status_obj.folder_status)
    )),
)


def _format_local_files(now, local_files):
    """
    Format some local files as unicode strings.

    :param datetime now: A time to use as current.

    :param dict local_files: A mapping from filenames to filenodes.  See
        ``_format_file_line`` for details of filenodes.

    :return: A generator of unicode strings describing the files.
    """
    for (name, child) in local_files.items():
        yield _format_file_line(now, name, child)


def _format_file_line(now, name, child):
    """
    Format one Tahoe-LAFS filenode as a unicode string.

    :param datetime now: A time to use as current.
    :param unicode name: The name of the file.

    :param child: Metadata describing the file.  The format is like the format
        of a filenode inside a dirnode's **children**.  See the Tahoe-LAFS Web
        API frontend documentation for details.

    :return unicode: Text roughly describing the filenode to a person.
    """
    captype, meta = child
    if captype != 'filenode':
        return u"%20s: error, should be a filecap (not %s)" % (name, captype)

    status = 'good'
    size = meta['size']
    created = datetime.fromtimestamp(meta['metadata']['tahoe']['linkcrtime'])
    version = meta['metadata']['version']
    nice_size = abbreviate_space(size)
    nice_created = abbreviate_time(now - created)
    return u"  %s (%s): %s, version=%s, created %s" % (
        name,
        nice_size,
        status,
        version,
        nice_created,
    )


def _format_remote_files(now, remote_files):
    """
    Format some files from peer DMDs as unicode strings.

    :param datetime now: A time to use as current.

    :param dict remote_files: A mapping from DMD names to dictionaries.  The
        inner dictionaries are like those which may be passed to
        ``_format_local_files``.

    :return: A generator of unicode strings describing the files.
    """
    for (name, children) in remote_files.items():
        yield u"  %s's remote:" % name
        for text in _format_local_files(now, children):
            yield text


def _format_magic_folder_status(now, magic_data):
    """
    Format details about magic folder activities as a unicode string.

    :param datetime now: A time to use as current.

    :param list[dict] magic_data: Activity to include in the result.  The
        elements are formatted like the result of
        ``magic_folder.web.status_for_item``.

    :return: A generator of unicode strings describing the activities.
    """
    if len(magic_data):
        uploads = [item for item in magic_data if item['kind'] == 'upload']
        downloads = [item for item in magic_data if item['kind'] == 'download']
        longest = max([len(item['path']) for item in magic_data])

        # maybe gate this with --show-completed option or something?
        uploads = [item for item in uploads if item['status'] != 'success']
        downloads = [item for item in downloads if item['status'] != 'success']

        if len(uploads):
            yield u""
            yield u"Uploads:"
            for item in uploads:
                yield _item_status(item, now, longest)

        if len(downloads):
            yield u""
            yield u"Downloads:"
            for item in downloads:
                yield _item_status(item, now, longest)

        for item in magic_data:
            if item['status'] == 'failure':
                yield u"Failed: {}".format(item)


class RunOptions(usage.Options):
    optParameters = [
        ("web-port", None, None,
         "String description of an endpoint on which to run the web interface (required).",
        ),
    ]

    def postOptions(self):
        if self['web-port'] is None:
            raise usage.UsageError("Must specify a listening endpoint with --web-port")


def run(options):
    """
    This is the long-running magic-folders function which performs
    synchronization between local and remote folders.
    """
    # XXX start logging
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
                ClientStandIn(self.tahoe_client, self.config),
                name,
                mf_config,
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
        fp = FilePath(self['config'])
        if not fp.exists():
            raise usage.UsageError(
                u"Configuration directory '{}' doesn't exist".format(fp.path)
            )
        return fp

    @property
    def config(self):
        """
        a GlobalConfigDatabase instance representing the current
        configuration location.
        """
        if self._config is None:
            self._config = load_global_configuration(self._config_path)
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
        ["status", None, StatusOptions, "Display status of uploads/downloads."],
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
        # ensure our configuration is valid
        _ = self.config

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
    "status": status,
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

    return react(main)
    if options.subCommand in ["init", "migrate", "show-config"]:
        pass
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
