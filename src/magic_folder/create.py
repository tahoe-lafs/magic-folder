# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder create command.
"""
import codecs
import os

from twisted.web.client import (
    Agent,
)

from .invite import (
    magic_folder_invite as _invite
)

from .join import (
    magic_folder_join as _join
)

from .common import (
    get_node_url
)

from treq.client import (
    HTTPClient,
)

from twisted.web.http import (
    OK,
)

from twisted.web.client import (
    readBody,
)

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue
)
from .frontends.magic_folder import (
    load_magic_folders,
    maybe_upgrade_magic_folders,
)

from allmydata.scripts.common_http import format_http_error
from allmydata.scripts.common import get_aliases
from allmydata import uri
from allmydata.util import fileutil
from allmydata.util.encodingutil import quote_output

# Until there is a web API, we replicate add_alias.
# https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3286
def add_line_to_aliasfile(aliasfile, alias, cap):
    # we use os.path.exists, rather than catching EnvironmentError, to avoid
    # clobbering the valuable alias file in case of spurious or transient
    # filesystem errors.
    if os.path.exists(aliasfile):
        f = codecs.open(aliasfile, "r", "utf-8")
        aliases = f.read()
        f.close()
        if not aliases.endswith("\n"):
            aliases += "\n"
    else:
        aliases = ""
    aliases += "%s: %s\n" % (alias, cap)
    f = codecs.open(aliasfile+".tmp", "w", "utf-8")
    f.write(aliases)
    f.close()
    fileutil.move_into_place(aliasfile+".tmp", aliasfile)

def _add_alias(node_directory, alias, cap):
    if u":" in alias:
        raise Exception("Alias names cannot contain colons.")
    if u" " in alias:
        raise Exception("Alias names cannot contain spaces.")

    old_aliases = get_aliases(node_directory)
    if alias in old_aliases:
        raise Exception("Alias {} already exists!".format(quote_output(alias)))

    aliasfile = os.path.join(node_directory, "private", "aliases")
    cap = uri.from_string_dirnode(cap).to_string()

    add_line_to_aliasfile(aliasfile, alias, cap)

    return 0

@inlineCallbacks
def tahoe_create_alias(node_directory, alias, treq):
    # mkdir+add_alias

    node_url = get_node_url(node_directory)
    if not node_url.endswith("/"):
        node_url += "/"
    url = node_url + "uri?t=mkdir"

    response = yield treq.post(url)
    if response.code != OK:
        raise Exception(format_http_error("Error", response))

    new_uri = yield readBody(response)

    _add_alias(node_directory, alias, new_uri)
    # probably check for others..

    # add_line_to_aliasfile(aliasfile, alias, new_uri)
    returnValue(0)

@inlineCallbacks
def magic_folder_create(alias, nickname, name, node_directory, local_dir, poll_interval, treq):

    # make sure we don't already have a magic-folder with this name before we create the alias
    maybe_upgrade_magic_folders(node_directory)
    folders = load_magic_folders(node_directory)

    if name in folders:
        raise Exception("Already have a magic-folder named '{}'".format(name))

    rc = yield tahoe_create_alias(node_directory, alias, treq)
    if rc != 0:
        raise Exception("Failed to create alias")

    if nickname is not None:
        # inviting itself as a client
        from twisted.internet import reactor
        treq = HTTPClient(Agent(reactor))

        try:
            invite_code = yield _invite(node_directory, alias, nickname, treq)
        except Exception:
            raise Exception("Failed to invite after create")

        rc = _join(invite_code, node_directory, local_dir, name, poll_interval)
        if rc != 0:
            raise Exception("Failed to join after create")

    returnValue(0)
