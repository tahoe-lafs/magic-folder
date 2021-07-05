# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Utilties for dealing with sqlite.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import sqlite3
from six import wraps
from twisted.python.compat import currentframe

import attr
import contextlib

__all__ = [
    "LockableDatabase",
    "RecusiveTransaction",
    "with_cursor",
]


@attr.s(auto_exc=True, frozen=True)
class RecusiveTransaction(Exception):
    function = attr.ib(default=None)
    caller = attr.ib(default=None)

    def __str__(self):
        message = "Tried to enter a recursive transaction"
        if self.function:
            message += " when calling '{}'".format(
                self.function
            )
        if self.caller:
            message += " from '{}'".format(
                self.caller
            )
        message += "."
        return message


@attr.s
class LockableDatabase(object):
    """
    Wrapper around an :py:`sqlite3.Connection`
    """

    _database = attr.ib(validator=attr.validators.instance_of(sqlite3.Connection))
    _in_transaction = attr.ib(
        default=False, init=False, validator=attr.validators.instance_of(bool)
    )

    @contextlib.contextmanager
    def transaction(self, function_name=None):
        """
        Context manager that starts a transaction and rollsback
        if an exception is raised.

        :param Optional[function_name]: The name of the calling function, used
            in error messages.

        :raises RecusiveTransaction: if used recusively.

        :return sqlite3.Cursor:
        """
        if self._in_transaction:
            frame = currentframe(2)
            if frame.f_code == with_cursor._wrapped_code:
                frame = frame.f_back
            caller = frame.f_code.co_name
            raise RecusiveTransaction(function=function_name, caller=caller)
        try:
            self._in_transaction = True
            with self._database:
                cursor = self._database.cursor()
                cursor.execute("BEGIN IMMEDIATE TRANSACTION")
                yield cursor
        finally:
            self._in_transaction = False


# XXX: with_cursor lacks unit tests, see:
#      https://github.com/LeastAuthority/magic-folder/issues/173
def with_cursor(f):
    """
    Decorate a method so it is automatically passed a cursor with an active
    transaction as the first positional argument.  If the method returns
    normally then the transaction will be committed.  Otherwise, the
    transaction will be rolled back.

    This decorated function expects the `_database` attribute of the object
    to have a py:`LockableDatabase` instance.

    If one decorated method is called from another such method, it will raise
    :py:`RecusiveTransaction`, as sqlite3 does not support recursive
    transactions.
    """
    function_name = f.__name__

    @wraps(f)
    def with_cursor(self, *a, **kw):
        with self._database.transaction(function_name) as cursor:
            return f(self, cursor, *a, **kw)

    return with_cursor

# We grab the code object of the wrapper function from with_cursor
# so we can provide a better error message for RecusiveTransaction
with_cursor._wrapped_code = with_cursor(lambda: None).__code__
