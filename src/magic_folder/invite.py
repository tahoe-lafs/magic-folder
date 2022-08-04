# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder invite command.
"""

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
    raise NotImplementedError
