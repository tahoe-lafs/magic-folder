from __future__ import print_function

import os
import sys
import urllib
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
    Message,
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
from allmydata.scripts.common_http import do_http, BadResponse
from allmydata.util import fileutil
from allmydata.util.abbreviate import abbreviate_space, abbreviate_time

from allmydata.scripts.common import (
    BasedirOptions,
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
from .web.magic_folder import (
    magic_folder_web_service,
)

from .status import (
    status as _status,
)

from .invite import (
    magic_folder_invite as _invite
)

from .create import (
    magic_folder_create as _create
)

from .join import (
    magic_folder_join as _join
)

from ._coverage import (
    coverage_service,
)

class CreateOptions(BasedirOptions):
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
        BasedirOptions.parseArgs(self)
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
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        self['node-url'] = fileutil.read(node_url_file).strip()

def _delegate_options(source_options, target_options):
    target_options.aliases = get_aliases(source_options['node-directory'])
    target_options["node-url"] = source_options["node-url"]
    target_options["node-directory"] = source_options["node-directory"]
    target_options["name"] = source_options["name"]
    target_options.stdin = MixedIO(u"")
    target_options.stdout = MixedIO()
    target_options.stderr = MixedIO()
    return target_options

@inlineCallbacks
def create(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, (unicode, NoneType)), nickname=options.nickname)
    precondition(isinstance(options.local_dir, (unicode, NoneType)), local_dir=options.local_dir)

    try:
        from twisted.internet import reactor
        treq = HTTPClient(Agent(reactor))

        name = options['name']
        nodedir = options["node-directory"]
        localdir = options.local_dir
        rc = yield _create(options.alias, options.nickname, name, nodedir, localdir, options["poll-interval"], treq)
    except Exception as e:
        print("%s" % str(e), file=options.stderr)
        returnValue(1)

    returnValue(rc)

class ListOptions(BasedirOptions):
    description = (
        "List all magic-folders this client has joined"
    )
    optFlags = [
        ("json", "", "Produce JSON output")
    ]


def list_(options):
    folders = load_magic_folders(options["node-directory"])
    if options["json"]:
        _list_json(options, folders)
        return 0
    _list_human(options, folders)
    return 0


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


class InviteOptions(BasedirOptions):
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
        BasedirOptions.parseArgs(self)
        alias = argv_to_unicode(alias)
        if not alias.endswith(u':'):
            raise usage.UsageError("An alias must end with a ':' character.")
        self.alias = alias[:-1]
        self.nickname = argv_to_unicode(nickname)
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        self['node-url'] = open(node_url_file, "r").read().strip()
        aliases = get_aliases(self['node-directory'])
        self.aliases = aliases

@inlineCallbacks
def invite(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, unicode), nickname=options.nickname)

    from twisted.internet import reactor
    treq = HTTPClient(Agent(reactor))

    try:
        invite_code = yield _invite(options["node-directory"], options.alias, options.nickname, treq)
        print("{}".format(invite_code), file=options.stdout)
    except Exception as e:
        print("magic-folder: {}".format(str(e)))
        returnValue(1)

    returnValue(0)

class JoinOptions(BasedirOptions):
    synopsis = "INVITE_CODE LOCAL_DIR"
    dmd_write_cap = ""
    magic_readonly_cap = ""
    optParameters = [
        ("poll-interval", "p", "60", "How often to ask for updates"),
        ("name", "n", "default", "Name of the magic-folder"),
    ]

    def parseArgs(self, invite_code, local_dir):
        BasedirOptions.parseArgs(self)

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

def join(options):
    """
    ``magic-folder join`` entrypoint.
    """
    try:
        invite_code = options.invite_code
        node_directory = options["node-directory"]
        local_directory = options.local_dir
        name = options['name']
        poll_interval = options["poll-interval"]

        rc = _join(invite_code, node_directory, local_directory, name, poll_interval)
    except Exception as e:
        print(e, file=options.stderr)
        return 1

    return rc

class LeaveOptions(BasedirOptions):
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
    existing_folders = load_magic_folders(options["node-directory"])

    if not existing_folders:
        print("No magic-folders at all", file=options.stderr)
        return 1

    if options["name"] not in existing_folders:
        print("No such magic-folder '{}'".format(options["name"]), file=options.stderr)
        return 1

    try:
        _leave(options["node-directory"], options["name"], existing_folders)
    except Exception as e:
        print("Warning: {}".format(str(e)))
        return 1

    return 0


class StatusOptions(BasedirOptions):
    synopsis = ""
    stdin = MixedIO(u"")
    optParameters = [
        ("name", "n", "default", "Name for the magic-folder to show status"),
    ]

    def parseArgs(self):
        BasedirOptions.parseArgs(self)
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        try:
            with open(node_url_file, "r") as f:
                self['node-url'] = f.read().strip()
        except EnvironmentError as e:
            raise usage.UsageError(
                "Could not read node url from {!r}: {!r}".format(
                    node_url_file,
                    e,
                ))


@log_call
def _get_json_for_fragment(options, fragment, method='GET', post_args=None):
    nodeurl = options['node-url']
    if nodeurl.endswith('/'):
        nodeurl = nodeurl[:-1]

    url = u'%s/%s' % (nodeurl, fragment)
    if method == 'POST':
        if post_args is None:
            raise ValueError("Must pass post_args= for POST method")
        body = urllib.urlencode(post_args)
    else:
        body = ''
        if post_args is not None:
            raise ValueError("post_args= only valid for POST method")
    resp = do_http(method, url, body=body)
    if isinstance(resp, BadResponse):
        # specifically NOT using format_http_error() here because the
        # URL is pretty sensitive (we're doing /uri/<key>).
        raise RuntimeError(
            "Failed to get json from '%s': %s" % (nodeurl, resp.error)
        )

    data = resp.read()
    Message.log(
        message_type=u"http",
        uri=url,
        response_body=data,
    )
    parsed = json.loads(data)
    if parsed is None:
        raise RuntimeError("No data from '%s'" % (nodeurl,))
    return parsed


def _get_json_for_cap(options, cap):
    return _get_json_for_fragment(
        options,
        'uri/%s?t=json' % urllib.quote(cap),
    )

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
    nodedir = options["node-directory"]
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
        ``magic_folder.web.magic_folder.status_for_item``.

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


class RunOptions(BasedirOptions):
    optParameters = [
        ("web-port", None, "tcp:9889",
         "String description of an endpoint on which to run the web interface.",
        ),
    ]


def main(options):
    """
    This is the long-running magic-folders function which performs
    synchronization between local and remote folders.
    """
    from twisted.internet import  reactor
    service = MagicFolderService.from_node_directory(
        reactor,
        options["node-directory"],
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
class MagicFolderService(MultiService):
    reactor = attr.ib()
    config = attr.ib()
    webport = attr.ib()
    magic_folder_configs = attr.ib()
    magic_folder_services = attr.ib(default=attr.Factory(dict))

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
        magic_folder_web_service(
            web_endpoint,
            self._get_magic_folder,
            self._get_auth_token,
        ).setServiceParent(self)

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

    def _get_magic_folder(self, name):
        return self.magic_folder_services[name]

    def _get_auth_token(self):
        return self.config.get_private_config("api_auth_token")

    @classmethod
    def from_node_directory(cls, reactor, nodedir, webport):
        config = read_config(nodedir, u"client.port")
        magic_folders = load_magic_folders(nodedir)
        return cls(reactor, config, webport, magic_folders)

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
        d.addCallback(lambda ignored: Deferred())
        return d

    def startService(self):
        MultiService.startService(self)
        ds = []
        for (name, mf_config) in self.magic_folder_configs.items():
            mf = MagicFolder.from_config(
                ClientStandIn(self.tahoe_client, self.config),
                name,
                mf_config,
            )
            self.magic_folder_services[name] = mf
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
        ["node-directory", "d", None, NODEDIR_HELP],
    ]

class MagicFolderCommand(BaseOptions):
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    subCommands = [
        ["create", None, CreateOptions, "Create a Magic Folder."],
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
    "create": create,
    "invite": invite,
    "join": join,
    "leave": leave,
    "status": status,
    "list": list_,
    "run": main,
}

def do_magic_folder(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    try:
        return f(so)
    except Exception as e:
        print(u"Error: {}".format(e), file=options.stderr)
        if options['debug']:
            raise

subCommands = [
    ["magic-folder", None, MagicFolderCommand,
     "Magic Folder subcommands: use 'tahoe magic-folder' for a list."],
]

dispatch = {
    "magic-folder": do_magic_folder,
}


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


def run():
    """
    Implement the *magic-folder* console script declared in ``setup.py``.

    :return: ``None``
    """
    from eliot import to_file
    from os import getpid
    to_file(open("magic-folder-cli.{}.eliot".format(getpid()), "w"))

    console_scripts = importlib_metadata.entry_points()["console_scripts"]
    magic_folder = list(
        script
        for script
        in console_scripts
        if script.name == "twist"
    )[0]
    argv = ["twist", "--log-level=debug", "--log-format=text", "magic_folder"] + sys.argv[1:]
    magic_folder.load()(argv)
