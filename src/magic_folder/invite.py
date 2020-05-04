# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder invite command.
"""

from allmydata.scripts.common import (
    get_aliases,
)

from allmydata import uri

from twisted.internet.defer import (
    returnValue,
    inlineCallbacks
)

from hyperlink import (
    DecodedURL,
)

from .common import (
    INVITE_SEPARATOR,
)

from .tahoe import (
    get_node_url,
    tahoe_mkdir,
    tahoe_mv,
)


@inlineCallbacks
def magic_folder_invite(node_directory, alias, nickname, treq):
    """
    Invite a user identified by the nickname to a folder owned by the alias

    :param unicode node_directory: The root of the Tahoe-LAFS node.

    :param unicode alias: The alias of the folder to which the invitation is
        being generated.

    :param unicode nickname: The nickname of the invitee.

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred[unicode]: A secret invitation code.
    """
    aliases = get_aliases(node_directory)[alias]
    nodeurl = get_node_url(node_directory)

    node_url = DecodedURL.from_text(unicode(nodeurl, 'utf-8'))

    # create an unlinked directory and get the dmd write-cap
    dmd_write_cap = yield tahoe_mkdir(node_url, treq)

    # derive a dmd read-only cap from it.
    dmd_readonly_cap = uri.from_string(dmd_write_cap).get_readonly().to_string()
    if dmd_readonly_cap is None:
        raise Exception("failed to diminish dmd write cap")

    # Now, we need to create a link to the nickname from inside the
    # collective to this read-cap. For that we will need to know
    # the write-cap of the collective (which is stored by the private/aliases
    # file in the node_directory) so that a link can be created inside it
    # to the .
    # To do that, we use tahoe ln dmd_read_cap <collective-write-cap>/<alias>

    magic_write_cap = get_aliases(node_directory)[alias]
    magic_readonly_cap = uri.from_string(magic_write_cap).get_readonly().to_string()

    # tahoe ln CLIENT_READCAP COLLECTIVE_WRITECAP/NICKNAME
    from_file = unicode(dmd_readonly_cap, 'utf-8')
    to_file = u"%s/%s" % (unicode(magic_write_cap, 'utf-8'), nickname)

    try:
        yield tahoe_mv(node_url, aliases, from_file, to_file, treq)
    except Exception:
        raise
        # return invite code, which is:
    #    magic_readonly_cap + INVITE_SEPARATOR + dmd_write_cap
    invite_code = "{}{}{}".format(magic_readonly_cap, INVITE_SEPARATOR, dmd_write_cap)

    returnValue(invite_code)
