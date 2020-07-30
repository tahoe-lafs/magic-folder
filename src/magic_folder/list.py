# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements ```magic-folder list``` command.
"""

import os

from twisted.internet import reactor
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.web.client import (
    Agent,
    readBody,
)

from hyperlink import  DecodedURL
from treq.client import HTTPClient
from allmydata.client import read_config


def get_magic_folder_api_base_url(node_directory):
    """
    :param str node_directory: A Tahoe client directory

    :returns: base URL for Magic Folder HTTP API, stored in
        ``node_directory/magic-folder.url``.
    """
    magic_folder_url_file = os.path.join(node_directory, u"magic-folder.url")
    with open(magic_folder_url_file, "r") as f:
        magic_folder_url = f.read().strip()
    return magic_folder_url


def get_magic_folder_api_token_from_cfg(config_directory):
    """
    Return token stored in magic folder config directory.

    The token returned here does not authorize us.  I'm unsure why.
    """
    from twisted.python.filepath import FilePath
    from .config import load_global_configuration

    path = FilePath(config_directory)
    return load_global_configuration(path).api_token


def get_magic_folder_api_token(node_directory):
    """
    Return token stored in ```node_directory/private/api_auth_token```.
    """
    config = read_config(node_directory, u"")
    return config.get_private_config("api_auth_token")


@inlineCallbacks
def magic_folder_list(node_directory):
    """
    List folders associated with a node.

    :param options: TODO

    :return: TODO JSON response from `/v1/magic-folder`.
    """
    base_url = get_magic_folder_api_base_url(node_directory)

    api_url = DecodedURL.from_text(
        unicode(base_url, 'utf-8')
    ).child(u'v1').child(u'magic-folder')

    api_token = get_magic_folder_api_token(node_directory)

    headers = {
        b"Authorization": u"Bearer {}".format(api_token).encode("ascii"),
    }

    response = yield HTTPClient(Agent(reactor)).get(
        api_url.to_uri().to_text().encode('ascii'),
        headers=headers,
    )

    result = yield readBody(response)

    returnValue(result)
