# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements ```magic-folder list``` command.
"""

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from .client import (
    create_magic_folder_client,
)


@inlineCallbacks
def magic_folder_list(config, include_secret_information=False):
    """
    List folders associated with a node.

    :param GlobalConfigDatabase config: our configuration

    :param bool include_secret_information: include sensitive private
        information (such as long-term keys) if True (default: False).

    :return: JSON response from `GET /v1/magic-folder`.
    """
    from twisted.internet import reactor

    client = create_magic_folder_client(reactor, config)
    result = yield client.list_folders(include_secret_information)
    returnValue(result)
