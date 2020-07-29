# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder invite command.
"""

from allmydata.scripts.common_http import format_http_error

from allmydata import uri

from twisted.internet.defer import (
    returnValue,
    inlineCallbacks
)
from twisted.python import (
    usage,
)
from twisted.web.http import (
    CONFLICT,
)

from .common import (
    INVITE_SEPARATOR,
)
from .tahoe_client import (
    create_tahoe_client,
)


@inlineCallbacks
def magic_folder_invite(config, folder_name, invitee_name, treq):
    """
    Invite a user identified by the nickname to a folder owned by the alias

    :param GlobalConfigDatabase config: our configuration

    :param unicode folder_name: The name of an existing magic-folder

    :param unicode invitee_name: The nickname for the new participant

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred[unicode]: A secret invitation code.
    """
    # XXX probably want to pass this in, instead of "treq"?
    tahoe_client = create_tahoe_client(config.tahoe_client_url, treq)

    # get configuration for this magic-folder (or error if it doesn't
    # exist)
    try:
        folder_config = config.get_magic_folder(folder_name)
    except ValueError:
        raise usage.UsageError(
            u"No magic-folder named '{}'".format(folder_name)
        )

    if not folder_config.is_admin():
        raise usage.UsageError(
            u"This device is not the administrator for '{}'".format(folder_name)
        )

    # create an unlinked directory and get the dmd write-cap
    dmd_write_cap = yield tahoe_client.create_mutable_directory()

    # derive a dmd read-only cap from it.
    dmd_readonly_cap = uri.from_string(dmd_write_cap.encode("utf8")).get_readonly().to_string()
    if dmd_readonly_cap is None:
        raise Exception("failed to diminish dmd write cap")

    # Now, we need to create a link to the nickname from inside the
    # collective to this read-cap. For that we will need to know the
    # write-cap of the collective (which will only be true if we're
    # the administrator device) so that a link can be created inside
    # it.

    # we already checked above that this is a write-cap using .is_admin()
    magic_write_cap = folder_config.collective_dircap
    magic_readonly_cap = uri.from_string(magic_write_cap.encode("utf8")).get_readonly().to_string()

    # ...we're adding a sub-directory to the global DMD; the name of
    # this sub-directory is the invitee's name and it points to the
    # mutable directory we created for them (which will be included in
    # the invite code)

    yield tahoe_client.add_entry_to_mutable_directory(
        magic_write_cap,
        invitee_name,
        dmd_readonly_cap,
    )

    invite_code = "{}{}{}".format(
        magic_readonly_cap,
        INVITE_SEPARATOR,
        dmd_write_cap,
    )

    returnValue(invite_code)
