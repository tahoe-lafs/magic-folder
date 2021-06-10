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
import unicodedata


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


class InvalidMagicFolderName(Exception):
    """
    The given magic folder name contains an invalid character.

    See :py:`valid_magic_folder_name` for details.
    """

    message = (
        u"Magic folder names cannot contain '/', '\\', "
        u"control characters or unassigned characters."
    )

    def __str__(self):
        return self.message


def valid_magic_folder_name(name):
    """
    Check if the magic folder name is valid.

    We disallow:

    - ``\0``, ``/``, and ``\\`` as they can cause issues with the HTTP API
    - control characters as they are not meant for display
    - non-characters (reserved and unassigned)
    - isolated surrogate characters as these are likely from invalid unicode
      (see PEP 383).

    :param unicode name: the name of the magic-folder to verify

    :raises ValueError: if this is an invalid magic folder name
    """
    if (
        u"\0" in name
        or u"/" in name
        or u"\\" in name
        or any((unicodedata.category(c) in ("Cc", "Cn", "Cs") for c in name))
    ):
        raise InvalidMagicFolderName(name)
