# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder invite command.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)


def magic_folder_invite(config, folder_name, suggested_invitee_name, treq):
    """
    Invite a user identified by the nickname to a folder owned by the alias

    :param GlobalConfigDatabase config: our configuration

    :param unicode folder_name: The name of an existing magic-folder

    :param unicode suggested_invitee_name: petname for the invited device

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred[unicode]: A secret magic-wormhole invitation code.
    """
    # FIXME TODO
    # see https://github.com/LeastAuthority/magic-folder/issues/232
    raise NotImplementedError
