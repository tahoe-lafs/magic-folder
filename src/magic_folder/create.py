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

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from allmydata.scripts.common import get_aliases
from allmydata import uri
from allmydata.util import fileutil
from allmydata.util.encodingutil import quote_output

from .snapshot import (
    create_local_author,
)
from .common import (
    get_node_url,
    tahoe_mkdir,
    BadResponseCode,
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
def magic_folder_create(config, name, author_name, local_dir, poll_interval, treq):
    """
    Create a magic-folder with the specified ``name`` and
    ``local_dir``.

    :param GlobalConfigDatabase config: Our configuration

    :param unicode name: The name of the magic-folder.

    :param unicode author_name: The name for our author

    :param FilePath local_dir: The directory on the filesystem that the user wants
        to sync between different computers.

    :param integer poll_interval: Periodic time interval after which the
        client polls for updates.

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred: ``None`` or an appropriate exception is raised.
    """

    if name in config.list_magic_folders():
        raise Exception("Already have a magic-folder named '{}'".format(name))

    # create our author
    author = create_local_author(author_name)

    # create an unlinked directory and get the dmd write-cap
    try:
        collective_write_cap = yield tahoe_mkdir(config.tahoe_client_url, treq)
    except BadResponseCode as e:
        raise RuntimeError(
            "Error from '{}' while creating mutable directory: {}".format(
                config.tahoe_client_url,
                e
            )
        )

    try:
        personal_write_cap = yield tahoe_mkdir(config.tahoe_client_url, treq)
    except BadResponseCode as e:
        raise RuntimeError(
            "Error from '{}' while creating mutable directory: {}".format(
                config.tahoe_client_url,
                e
            )
        )

    # create our "state" directory for this magic-folder (could be
    # configurable in the future)
    state_dir = config.get_default_state_path(name)
    try:
        config.create_magic_folder(
            name,
            local_dir,
            state_dir,
            author,
            collective_write_cap,
            personal_write_cap,
            poll_interval,
        )
    except Exception:
        state_dir.remove()
        raise
