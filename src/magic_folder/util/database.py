# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Utilties for dealing with sqlite.
"""

import contextlib
import inspect
import sqlite3

import attr
from functools import wraps
from twisted.python.compat import currentframe

__all__ = [
    "LockableDatabase",
    "RecusiveTransaction",
    "WithCursorGenerator",
    "with_cursor",
]


@attr.s(auto_exc=True)
class WithCursorGenerator(TypeError):
    """
    :py:`with_cursor` cannot be used on a generator.

    As it is currently implemented, the transaction would be commited before
    any code in the decorated generator ran. Even if we special cased generators,
    having the transaction stay open until the generator completes, that loses
    the proprerty of `with_cursor` being transparent and atomic from the point
    of view of the calller.
    """

    function = attr.ib()

    def __str__(self):
        return "'with_cursor' cannot decorate a generator function: {!r}".format(
            self.function
        )


@attr.s(auto_exc=True)
class _LockableDatabaseTransactionError(Exception):
    """
    An error from calling :py:`LockableDatabase.transaction`.
    """

    message = attr.ib(validator=attr.validators.instance_of(str))
    function = attr.ib(default=None)
    caller = attr.ib(default=None)

    @classmethod
    def _from_stack(cls, function=None):
        """
        Should only be called by py:`LockableDatabase.transaction`.

        Find the caller of and construct an exception referencing it. This will
        skip over the wrapper from :py:`with_cursor`.

        This must be called on a subclass that defines `.message`.
        """
        # There are 2 stack frames between this function
        # 0. _LockableDatabaseTransactionError.from_stack
        # 1. LockableDatabase.transaction
        # 2. contextlib.contextmanger's __enter__
        # 3. Our caller (potentially with_cursor)
        frame = currentframe(3)
        if frame.f_code == with_cursor._wrapped_code:
            frame = frame.f_back
        caller = frame.f_code.co_name
        return cls(caller=caller, function=function)

    def __str__(self):
        message = self.message
        if self.function:
            message += " when calling '{}'".format(self.function)
        if self.caller:
            message += " from '{}'".format(self.caller)
        message += "."
        return message


@attr.s(auto_exc=True)
class ClosedDatabase(_LockableDatabaseTransactionError):
    """
    :py:`with_cursor` or :py:`LockableDatabase.transaction` was called on a
    database that has been closed.
    """

    message = attr.ib(init=False, default="Tried to operate on a closed database")


@attr.s(auto_exc=True)
class RecusiveTransaction(_LockableDatabaseTransactionError):
    """
    :py:`with_cursor` or :py:`LockableDatabase.transaction` was called while a
    transaction was already open.
    """

    message = attr.ib(init=False, default="Tried to enter a recursive transaction")


@attr.s
class LockableDatabase(object):
    """
    Wrapper around an :py:`sqlite3.Connection`
    """

    _database = attr.ib(validator=attr.validators.instance_of(sqlite3.Connection))
    _in_transaction = attr.ib(
        default=False, init=False, validator=attr.validators.instance_of(bool)
    )
    _closed = attr.ib(
        default=False, init=False, validator=attr.validators.instance_of(bool)
    )

    def close(self):
        """
        Close the connection to the database.
        """
        self._closed = True
        self._database.close()

    @contextlib.contextmanager
    def transaction(self, function_name=None):
        """
        Context manager that starts a transaction and rolls back
        if an exception is raised.

        :param Optional[str] function_name: The name of the calling function,
            used in error messages.

        :raises ClosedDatabase: if the database has been closed.
        :raises RecusiveTransaction: if used recusively.

        :return sqlite3.Cursor:
        """
        if self._closed:
            raise ClosedDatabase._from_stack(function=function_name)
        if self._in_transaction:
            raise RecusiveTransaction._from_stack(function=function_name)
        try:
            self._in_transaction = True
            with self._database:
                cursor = self._database.cursor()
                cursor.execute("BEGIN IMMEDIATE TRANSACTION")
                yield cursor
        finally:
            self._in_transaction = False


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

    if inspect.isgeneratorfunction(f):
        raise WithCursorGenerator(function_name)

    @wraps(f)
    def with_cursor(self, *a, **kw):
        with self._database.transaction(function_name) as cursor:
            return f(self, cursor, *a, **kw)

    return with_cursor


# We grab the code object of the wrapper function from with_cursor
# so we can provide a better error message for RecusiveTransaction
with_cursor._wrapped_code = with_cursor(lambda: None).__code__
