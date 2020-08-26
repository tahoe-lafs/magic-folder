# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Common functions and types used by other modules.
"""
from contextlib import contextmanager

from eliot.twisted import (
    inline_callbacks,
)

from twisted.web.client import (
    readBody,
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


@contextmanager
def atomic_makedirs(path):
    """
    Call `path.makedirs()` but if an error occurs before this
    context-manager exits we will delete the directory.

    :param FilePath path: the directory/ies to create
    """
    path_b = path.asBytesMode("utf-8")
    path_b.makedirs()
    try:
        yield path
    except Exception:
        # on error, clean up our directory
        path_b.remove()
        # ...and pass on the error
        raise
