# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder create command.
"""
import codecs
import os


from hyperlink import (
    DecodedURL,
)

from treq.client import (
    HTTPClient,
)

from twisted.web.client import (
    Agent,
)

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from allmydata.scripts.common import get_aliases
from allmydata import uri
from allmydata.util import fileutil
from allmydata.util.encodingutil import quote_output

from .magic_folder import (
    load_magic_folders,
    maybe_upgrade_magic_folders,
)

from .invite import (
    magic_folder_invite as _invite
)

from .join import (
    magic_folder_join as _join
)

from .common import (
    get_node_url,
    tahoe_mkdir,
)

# Until there is a web API, we replicate add_alias.
# https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3286
def add_line_to_aliasfile(aliasfile, alias, cap):
    # we use os.path.exists, rather than catching EnvironmentError, to avoid
    # clobbering the valuable alias file in case of spurious or transient
    # filesystem errors.
    if os.path.exists(aliasfile):
        with codecs.open(aliasfile, "r", "utf-8") as f:
            aliases = f.read()
        if not aliases.endswith("\n"):
            aliases += "\n"
    else:
        aliases = ""
    aliases += "%s: %s\n" % (alias, cap)
    with codecs.open(aliasfile+".tmp", "w", "utf-8") as f:
        f.write(aliases)
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

    nodeurl = get_node_url(node_directory)

    try:
        node_url = DecodedURL.from_text(unicode(nodeurl, 'utf-8'))
        new_uri = yield tahoe_mkdir(node_url, treq)
    except Exception:
        raise

    _add_alias(node_directory, alias, new_uri)

    returnValue(0)

@inlineCallbacks
def magic_folder_create(alias, nickname, name, node_directory, local_dir, poll_interval, treq):
    """
    Create a magic-folder with the specified ``name`` (or ``default``
    if not specified) and optionally invite the client and join with
    the specified ``nickname`` and the specified ``local_dir``.

    :param unicode alias: The alias of the folder to which the invitation is
        being generated.

    :param unicode nickname: The nickname of the invitee.

    :param unicode name: The name of the magic-folder.

    :param unicode node_directory: The root of the Tahoe-LAFS node.

    :param unicode local_dir: The directory on the filesystem that the user wants
        to sync between different computers.

    :param integer poll_interval: Periodic time interval after which the
        client polls for updates.

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred[integer]: A status code of 0 for a successful execution. Otherwise
        an appropriate exception is raised.
    """

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
        except Exception as e:
            raise Exception("Failed to invite after create: {}".format(str(e)))

        rc = _join(invite_code, node_directory, local_dir, name, poll_interval)
        if rc != 0:
            raise Exception("Failed to join after create")

    returnValue(0)
