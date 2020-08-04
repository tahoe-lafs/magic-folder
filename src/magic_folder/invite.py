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
def magic_folder_invite(config, folder_name, treq):
    """
    Invite a user identified by the nickname to a folder owned by the alias

    :param GlobalConfigDatabase config: our configuration

    :param unicode folder_name: The name of an existing magic-folder

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred[unicode]: A secret magic-wormhole invitation code.
    """
    # FIXME TODO
    # see https://github.com/LeastAuthority/magic-folder/issues/232
    raise NotImplemented
