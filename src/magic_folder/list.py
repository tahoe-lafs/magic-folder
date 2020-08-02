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


def get_magic_folder_api_base_url_from_node_dir(node_directory):
    """
    Get HTTP base URL.

    :param str node_directory: A Tahoe node directory.

    :returns: base URL for Magic Folder HTTP API, stored in
        ``node_directory/magic-folder.url``.
    """
    magic_folder_url_file = os.path.join(node_directory, u"magic-folder.url")
    with open(magic_folder_url_file, "r") as f:
        magic_folder_url = f.read().strip()
    return magic_folder_url

def get_magic_folder_api_token_from_node_dir(node_directory):
    """
    Get token stored in ```node_directory/private/api_auth_token```.

    :param str node_directory: a Tahoe node_directory.

    :returns: an API auth token.
    """
    config = read_config(node_directory, u"")
    return config.get_private_config("api_auth_token")


def get_magic_folder_api_base_url_config_dir(config_directory):
    """
    :param str config_directory: a Magic Folder configuration directory.

    :returns: base URL for the given Magic Folder instance.
    """
    cfg = load_global_configuration(FilePath(config_directory))
    return endpoint_description_to_http_api_root(cfg.api_endpoint)


def get_magic_folder_api_token_from_config_dir(config_directory):
    """
    Get token stored in magic folder config directory.

    The token returned here does not authorize us.  I'm unsure why.

    :param str config_directory: a Magic Folder configuration directory

    :returns: an API auth token.
    """
    from twisted.python.filepath import FilePath
    from .config import load_global_configuration

    path = FilePath(config_directory)
    return load_global_configuration(path).api_token


@inlineCallbacks
def magic_folder_list(node_directory):
    """
    List folders associated with a node.

    :param options: TODO

    :return: JSON response from `GET /v1/magic-folder`.
    """
    base_url = get_magic_folder_api_base_url_from_node_dir(node_directory)

    api_url = DecodedURL.from_text(
        unicode(base_url, 'utf-8')
    ).child(u'v1').child(u'magic-folder')

    api_token = get_magic_folder_api_token_from_node_dir(node_directory)

    headers = {
        b"Authorization": u"Bearer {}".format(api_token).encode("ascii"),
    }

    response = yield HTTPClient(Agent(reactor)).get(
        api_url.to_uri().to_text().encode('ascii'),
        headers=headers,
    )

    result = yield readBody(response)

    returnValue(result)
