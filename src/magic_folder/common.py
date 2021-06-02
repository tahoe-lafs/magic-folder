# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Common functions and types used by other modules.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)

from contextlib import contextmanager

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
