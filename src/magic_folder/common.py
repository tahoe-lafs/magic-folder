# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Common functions and types used by other modules.
"""

from contextlib import contextmanager
import unicodedata

import attr

from twisted.web import http


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


def does_not_have_keys(*keys):
    def validator(inst, attr, value):
        invalid_keys = [key for key in keys if key in value]
        if invalid_keys:
            raise ValueError(
                "'{name}' must not have {invalid_keys} keys.".format(
                    name=attr.name,
                    invalid_keys=invalid_keys,
                )
            )
    return validator


# this cannot be frozen=True because of Twisted
@attr.s(auto_exc=True)
class APIError(Exception):
    """
    An error to be reported from the API.

    :ivar reason unicode: The message to be returned as the ``reason`` key of
       the error response.
    :ivar code int: The HTTP status code to use for this error.
    """

    code = attr.ib(
        validator=attr.validators.optional(attr.validators.instance_of(int)),
    )
    reason = attr.ib(validator=attr.validators.instance_of(str))
    extra_fields = attr.ib(
        default=None,
        validator=attr.validators.optional(
            attr.validators.and_(
                attr.validators.instance_of(dict),
                does_not_have_keys("reason"),
            ),
        )
    )

    @classmethod
    def from_exception(cls, code, exception, prefix=None):
        """
        Return an exception with the given code, and reason from the given exception.

        :param code int: The HTTP status code to use for this error.
        :param exception Exception: The exception to get the error message from.
        :param prefix unicode: A prefix to add to the error message.

        :returns APIError: An error with the given error code and a message that is
            ``"{prefix}: {exception message}"``.
        """
        if prefix is not None:
            reason = u"{}: {}".format(prefix, exception)
        else:
            reason = u"{}".format(exception)
        return cls(code=code, reason=reason)

    def to_json(self):
        """
        :return: a representation of this error suitable for JSON encoding.
        """
        result = {"reason": self.reason}
        if self.extra_fields:
            result.update(self.extra_fields)
        return result

    def __str__(self):
        return self.reason


@attr.s(auto_exc=True)
class NoSuchMagicFolder(APIError):
    """
    There is not a magic folder of the given name.
    """

    name = attr.ib(validator=attr.validators.instance_of(str))
    code = attr.ib(
        init=False,
        default=http.NOT_FOUND,
    )
    reason = attr.ib(
        init=False,
        default=attr.Factory(
            lambda self: u"No such magic-folder '{}'".format(self.name),
            takes_self=True,
        ),
    )
    extra_fields = attr.ib(init=False, default=None)


@contextmanager
def atomic_makedirs(path):
    """
    Call `path.makedirs()` but if an error occurs before this
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


@attr.s(auto_exc=True)
class InvalidMagicFolderName(APIError):
    """
    The given magic folder name contains an invalid character.

    See :py:`valid_magic_folder_name` for details.
    """

    message = (
        u"Magic folder names cannot contain '/', '\\', "
        u"control characters or unassigned characters."
    )

    name = attr.ib(validator=attr.validators.instance_of(str))
    code = attr.ib(
        default=http.BAD_REQUEST,
        validator=attr.validators.optional(attr.validators.instance_of(int)),
    )
    reason = attr.ib(init=False, default=message)
    extra_fields = attr.ib(init=False, default=None)


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
