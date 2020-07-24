# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Common functions and types used by other modules.
"""
import os
from contextlib import contextmanager

from eliot.twisted import (
    inline_callbacks,
)

from twisted.internet.defer import (
    returnValue,
    inlineCallbacks
)

from twisted.web.client import (
    readBody,
)

from twisted.web.http import (
    OK,
)

INVITE_SEPARATOR = "+"

class BadFolderName(Exception):
    """
    An operation was attempted on a magic folder using a name which does not
    exist.
    """
    def __str__(self):
        return "Folder named {!r} not found.".format(self.args[0])


class BadResponseCode(Exception):
    """
    An HTTP request received a response code which does not allow an operation
    to progress further.
    """
    def __str__(self):
        return "Request for {!r} received unexpected response code {!r}:\n{}".format(
            self.args[0].to_text(),
            self.args[1],
            self.args[2],
        )

class BadMetadataResponse(Exception):
    """
    An attempt to load some metadata about something received a response that
    cannot be interpreted as that metadata.
    """


class BadDirectoryCapability(Exception):
    """
    A capability which was expected to refer to a directory object instead did
    not.
    """

def _check_result(result):
    """
    Inspect a decoded response body to see if it is an error.

    :param result: An object loaded from a JSON response to an API endpoint.

    :raise BadMetadataResponse: If ``result`` represents an error.

    :return: Exactly ``result`` if it does not represent an error.
    """
    if isinstance(result, dict) and u"error" in result:
        raise BadMetadataResponse(result)
    return result

@inline_callbacks
def bad_response(url, response):
    """
    Convert an ``IResponse`` with an unexpected status code into a
    ``BadResponseCode`` containing the URL, response code, and response body.

    :param DecodedURL url: The URL to include in the exception.

    :param IResponse response: The response from which the body can be read.

    :return: A ``Deferred`` which fails with ``BadResponseCode``.
    """
    body = yield readBody(response)
    raise BadResponseCode(url, response.code, body)


def get_node_url(node_directory):
    """
    :param str node_directory: A Tahoe client directory

    :returns: the base URL for the Tahoe given client.
    """
    node_url_file = os.path.join(node_directory, u"node.url")
    with open(node_url_file, "r") as f:
        node_url = f.read().strip()
    return node_url


@inlineCallbacks
def tahoe_mkdir(nodeurl, treq):
    """
    :param DecodedURL nodeurl: The web endpoint of the Tahoe-LAFS client
        associated with the magic-wormhole client.

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to
        make the queries.

    :return Deferred[unicode]: The writecap associated with the newly created unlinked
        directory.
    """
    url = nodeurl.child(
        u"uri",
    ).add(
        u"t",
        u"mkdir",
    )

    post_uri = url.to_uri().to_text().encode("ascii")
    response = yield treq.post(post_uri)
    if response.code != OK:
        returnValue((yield bad_response(url, response)))

    result = yield readBody(response)
    # emit its write-cap
    returnValue(result)


@contextmanager
def atomic_makedirs(path):
    """
    Call `path.makedirs()` but if an errors occurs before this
    context-manager exits we will delete the directory.

    :param FilePath path: the directory/ies to create
    """
    path.makedirs()
    try:
        yield path
    except Exception:
        # on error, clean up our directory
        path.remove()
        # ...and pass on the error
        raise
