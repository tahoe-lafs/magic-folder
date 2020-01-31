from __future__ import print_function

import os
import sys
import urllib
from types import NoneType
from six.moves import cStringIO as StringIO
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
    URL,
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

from twisted.web.client import (
    Agent,
    readBody,
    FileBodyProducer,
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
    returnValue,
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
from allmydata.scripts import (
    tahoe_mv,
    tahoe_mkdir,
    tahoe_add_alias,
)
from allmydata.util.encodingutil import (
    argv_to_abspath,
    argv_to_unicode,
    to_str,
    quote_local_unicode_path,
)
from allmydata.scripts.common_http import do_http, BadResponse
from allmydata.util import fileutil
from allmydata import uri
from allmydata.util.abbreviate import abbreviate_space, abbreviate_time

from allmydata.scripts.common import (
    BasedirOptions,
    get_aliases,
)
from allmydata.scripts.cli import (
    MakeDirectoryOptions,
    LnOptions,
    CreateAliasOptions,
)
from allmydata.client import (
    read_config,
)

from ..frontends.magic_folder import (
    MagicFolder,
    load_magic_folders,
    save_magic_folders,
    maybe_upgrade_magic_folders,
)

INVITE_SEPARATOR = "+"

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
    target_options.stdin = StringIO("")
    target_options.stdout = StringIO()
    target_options.stderr = StringIO()
    return target_options

def create(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, (unicode, NoneType)), nickname=options.nickname)
    precondition(isinstance(options.local_dir, (unicode, NoneType)), local_dir=options.local_dir)

    # make sure we don't already have a magic-folder with this name before we create the alias
    maybe_upgrade_magic_folders(options["node-directory"])
    folders = load_magic_folders(options["node-directory"])
    if options['name'] in folders:
        print("Already have a magic-folder named '{}'".format(options['name']), file=options.stderr)
        return 1

    # create an alias; this basically just remembers the cap for the
    # master directory
    create_alias_options = _delegate_options(options, CreateAliasOptions())
    create_alias_options.alias = options.alias

    rc = tahoe_add_alias.create_alias(create_alias_options)
    if rc != 0:
        print(create_alias_options.stderr.getvalue(), file=options.stderr)
        return rc
    print(create_alias_options.stdout.getvalue(), file=options.stdout)

    if options.nickname is not None:
        print(u"Inviting myself as client '{}':".format(options.nickname), file=options.stdout)
        invite_options = _delegate_options(options, InviteOptions())
        invite_options.alias = options.alias
        invite_options.nickname = options.nickname
        invite_options['name'] = options['name']
        rc = invite(invite_options)
        if rc != 0:
            print(u"magic-folder: failed to invite after create\n", file=options.stderr)
            print(invite_options.stderr.getvalue(), file=options.stderr)
            return rc
        invite_code = invite_options.stdout.getvalue().strip()
        print(u"  created invite code", file=options.stdout)
        join_options = _delegate_options(options, JoinOptions())
        join_options['poll-interval'] = options['poll-interval']
        join_options.nickname = options.nickname
        join_options.local_dir = options.local_dir
        join_options.invite_code = invite_code
        rc = join(join_options)
        if rc != 0:
            print(u"magic-folder: failed to join after create\n", file=options.stderr)
            print(join_options.stderr.getvalue(), file=options.stderr)
            return rc
        print(u"  joined new magic-folder", file=options.stdout)
        print(
            u"Successfully created magic-folder '{}' with alias '{}:' "
            u"and client '{}'\nYou must re-start your node before the "
            u"magic-folder will be active."
        .format(options['name'], options.alias, options.nickname), file=options.stdout)
    return 0


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
    stdin = StringIO("")
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


def invite(options):
    precondition(isinstance(options.alias, unicode), alias=options.alias)
    precondition(isinstance(options.nickname, unicode), nickname=options.nickname)

    mkdir_options = _delegate_options(options, MakeDirectoryOptions())
    mkdir_options.where = None

    rc = tahoe_mkdir.mkdir(mkdir_options)
    if rc != 0:
        print("magic-folder: failed to mkdir\n", file=options.stderr)
        return rc

    # FIXME this assumes caps are ASCII.
    dmd_write_cap = mkdir_options.stdout.getvalue().strip()
    dmd_readonly_cap = uri.from_string(dmd_write_cap).get_readonly().to_string()
    if dmd_readonly_cap is None:
        print("magic-folder: failed to diminish dmd write cap\n", file=options.stderr)
        return 1

    magic_write_cap = get_aliases(options["node-directory"])[options.alias]
    magic_readonly_cap = uri.from_string(magic_write_cap).get_readonly().to_string()

    # tahoe ln CLIENT_READCAP COLLECTIVE_WRITECAP/NICKNAME
    ln_options = _delegate_options(options, LnOptions())
    ln_options.from_file = unicode(dmd_readonly_cap, 'utf-8')
    ln_options.to_file = u"%s/%s" % (unicode(magic_write_cap, 'utf-8'), options.nickname)
    rc = tahoe_mv.mv(ln_options, mode="link")
    if rc != 0:
        print("magic-folder: failed to create link\n", file=options.stderr)
        print(ln_options.stderr.getvalue(), file=options.stderr)
        return rc

    # FIXME: this assumes caps are ASCII.
    print("%s%s%s" % (magic_readonly_cap, INVITE_SEPARATOR, dmd_write_cap), file=options.stdout)
    return 0

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
    fields = options.invite_code.split(INVITE_SEPARATOR)
    if len(fields) != 2:
        raise usage.UsageError("Invalid invite code.")
    magic_readonly_cap, dmd_write_cap = fields

    maybe_upgrade_magic_folders(options["node-directory"])
    existing_folders = load_magic_folders(options["node-directory"])

    if options['name'] in existing_folders:
        print("This client already has a magic-folder named '{}'".format(options['name']), file=options.stderr)
        return 1

    db_fname = os.path.join(
        options["node-directory"],
        u"private",
        u"magicfolder_{}.sqlite".format(options['name']),
    )
    if os.path.exists(db_fname):
        print("Database '{}' already exists; not overwriting".format(db_fname), file=options.stderr)
        return 1

    folder = {
        u"directory": options.local_dir.encode('utf-8'),
        u"collective_dircap": magic_readonly_cap,
        u"upload_dircap": dmd_write_cap,
        u"poll_interval": options["poll-interval"],
    }
    existing_folders[options["name"]] = folder

    save_magic_folders(options["node-directory"], existing_folders)
    return 0


class LeaveOptions(BasedirOptions):
    synopsis = "Remove a magic-folder and forget all state"
    optParameters = [
        ("name", "n", "default", "Name of magic-folder to leave"),
    ]


def leave(options):
    existing_folders = load_magic_folders(options["node-directory"])

    if not existing_folders:
        print("No magic-folders at all", file=options.stderr)
        return 1

    if options["name"] not in existing_folders:
        print("No such magic-folder '{}'".format(options["name"]), file=options.stderr)
        return 1

    privdir = os.path.join(options["node-directory"], u"private")
    db_fname = os.path.join(privdir, u"magicfolder_{}.sqlite".format(options["name"]))

    # delete from YAML file and re-write it
    del existing_folders[options["name"]]
    save_magic_folders(options["node-directory"], existing_folders)

    # delete the database file
    try:
        fileutil.remove(db_fname)
    except Exception as e:
        print("Warning: unable to remove %s due to %s: %s"
            % (quote_local_unicode_path(db_fname), e.__class__.__name__, str(e)), file=options.stderr)

    # if this was the last magic-folder, disable them entirely
    if not existing_folders:
        parser = SafeConfigParser()
        parser.read(os.path.join(options["node-directory"], u"tahoe.cfg"))
        parser.remove_section("magic_folder")
        with open(os.path.join(options["node-directory"], u"tahoe.cfg"), "w") as f:
            parser.write(f)

    return 0


class StatusOptions(BasedirOptions):
    synopsis = ""
    stdin = StringIO("")
    optParameters = [
        ("name", "n", "default", "Name for the magic-folder to show status"),
    ]

    def parseArgs(self):
        BasedirOptions.parseArgs(self)
        node_url_file = os.path.join(self['node-directory'], u"node.url")
        with open(node_url_file, "r") as f:
            self['node-url'] = f.read().strip()


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
    parsed = json.loads(data)
    if parsed is None:
        raise RuntimeError("No data from '%s'" % (nodeurl,))
    return parsed


def _get_json_for_cap(options, cap):
    return _get_json_for_fragment(
        options,
        'uri/%s?t=json' % urllib.quote(cap),
    )

def _print_item_status(item, now, longest):
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

    print("  %s: %s" % (paddedname, prog))


def status(options):
    nodedir = options["node-directory"]
    stdout, stderr = options.stdout, options.stderr
    magic_folders = load_magic_folders(os.path.join(options["node-directory"]))

    with open(os.path.join(nodedir, u'private', u'api_auth_token'), 'rb') as f:
        token = f.read()

    print("Magic-folder status for '{}':".format(options["name"]), file=stdout)

    if options["name"] not in magic_folders:
        raise Exception(
            "No such magic-folder '{}'".format(options["name"])
        )

    dmd_cap = magic_folders[options["name"]]["upload_dircap"]
    collective_readcap = magic_folders[options["name"]]["collective_dircap"]

    # do *all* our data-retrievals first in case there's an error
    try:
        dmd_data = _get_json_for_cap(options, dmd_cap)
        remote_data = _get_json_for_cap(options, collective_readcap)
        magic_data = _get_json_for_fragment(
            options,
            'magic_folder?t=json',
            method='POST',
            post_args=dict(
                t='json',
                name=options["name"],
                token=token,
            )
        )
    except Exception as e:
        print("failed to retrieve data: %s" % str(e), file=stderr)
        return 2

    for d in [dmd_data, remote_data, magic_data]:
        if isinstance(d, dict) and 'error' in d:
            print("Error from server: %s" % d['error'], file=stderr)
            print("This means we can't retrieve the remote shared directory.", file=stderr)
            return 3

    captype, dmd = dmd_data
    if captype != 'dirnode':
        print("magic_folder_dircap isn't a directory capability", file=stderr)
        return 2

    now = datetime.now()

    print("Local files:", file=stdout)
    for (name, child) in dmd['children'].items():
        captype, meta = child
        status = 'good'
        size = meta['size']
        created = datetime.fromtimestamp(meta['metadata']['tahoe']['linkcrtime'])
        version = meta['metadata']['version']
        nice_size = abbreviate_space(size)
        nice_created = abbreviate_time(now - created)
        if captype != 'filenode':
            print("%20s: error, should be a filecap" % name, file=stdout)
            continue
        print("  %s (%s): %s, version=%s, created %s" % (name, nice_size, status, version, nice_created), file=stdout)

    print(file=stdout)
    print("Remote files:", file=stdout)

    captype, collective = remote_data
    for (name, data) in collective['children'].items():
        if data[0] != 'dirnode':
            print("Error: '%s': expected a dirnode, not '%s'" % (name, data[0]), file=stdout)
        print("  %s's remote:" % name, file=stdout)
        dmd = _get_json_for_cap(options, data[1]['ro_uri'])
        if isinstance(dmd, dict) and 'error' in dmd:
            print("    Error: could not retrieve directory", file=stdout)
            continue
        if dmd[0] != 'dirnode':
            print("Error: should be a dirnode", file=stdout)
            continue
        for (n, d) in dmd[1]['children'].items():
            if d[0] != 'filenode':
                print("Error: expected '%s' to be a filenode." % (n,), file=stdout)

            meta = d[1]
            status = 'good'
            size = meta['size']
            created = datetime.fromtimestamp(meta['metadata']['tahoe']['linkcrtime'])
            version = meta['metadata']['version']
            nice_size = abbreviate_space(size)
            nice_created = abbreviate_time(now - created)
            print("    %s (%s): %s, version=%s, created %s" % (n, nice_size, status, version, nice_created), file=stdout)

    if len(magic_data):
        uploads = [item for item in magic_data if item['kind'] == 'upload']
        downloads = [item for item in magic_data if item['kind'] == 'download']
        longest = max([len(item['path']) for item in magic_data])

        # maybe gate this with --show-completed option or something?
        uploads = [item for item in uploads if item['status'] != 'success']
        downloads = [item for item in downloads if item['status'] != 'success']

        if len(uploads):
            print()
            print("Uploads:", file=stdout)
            for item in uploads:
                _print_item_status(item, now, longest)

        if len(downloads):
            print()
            print("Downloads:", file=stdout)
            for item in downloads:
                _print_item_status(item, now, longest)

        for item in magic_data:
            if item['status'] == 'failure':
                print("Failed:", item, file=stdout)

    return 0


class RunOptions(BasedirOptions):
    pass


def main(options):
    """
    This is the long-running magic-folders function which performs
    synchronization between local and remote folders.
    """
    from twisted.internet import  reactor
    service = MagicFolderService.from_node_directory(
        reactor,
        options["node-directory"],
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
class MagicFolderService(MultiService):
    reactor = attr.ib()
    config = attr.ib()
    magic_folder_configs = attr.ib()
    magic_folder_services = attr.ib(default=attr.Factory(dict))

    def __attrs_post_init__(self):
        MultiService.__init__(self)
        self.tahoe_client = TahoeClient(
            URL.from_text(self.config.get_config_from_file(b"node.url").decode("utf-8")),
            Agent(self.reactor),
        )

    @classmethod
    def from_node_directory(cls, reactor, nodedir):
        config = read_config(nodedir, u"client.port")
        magic_folders = load_magic_folders(nodedir)
        return cls(reactor, config, magic_folders)

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
        print("Starting")
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
        convergence_s = self.config.get_private_config('convergence')
        self.convergence = base32.a2b(convergence_s)

    def create_node_from_uri(self, uri):
        return Node(self.tahoe_client, from_string(uri))


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
        print("Listing directory contents for {}".format(uri))
        response = yield self.agent.request(
            b"GET",
            self.node_uri.child(u"uri", uri.to_string().decode("ascii")).add(u"t", u"json").to_uri().to_text().encode("ascii"),
        )
        if response.code != 200:
            raise Exception("Error response from list endpoint: {}".format(response))

        kind, dirinfo = json.loads((yield readBody(response)))
        if kind != u"dirnode":
            raise ValueError("Object is a {}, not a directory".format(kind))
        print("Directory contents are {}".format(dirinfo[u"children"]))
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
            yield self.agent.request(
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

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    def getSynopsis(self):
        return "Usage: tahoe [global-options] magic-folder"
    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += (
            "Please run e.g. 'tahoe magic-folder create --help' for more "
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
        print("Error: %s" % (e,), file=options.stderr)
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
    return _MagicFolderService(options)


def run():
    """
    Implement the *magic_folder* console script declared in ``setup.py``.

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
