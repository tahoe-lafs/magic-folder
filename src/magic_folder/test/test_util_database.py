# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Utilties for dealing with sqlite.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import sqlite3

import attr
from testtools import ExpectedException
from testtools.matchers import Equals

from ..util.database import LockableDatabase, RecusiveTransaction, with_cursor
from .common import SyncTestCase


@attr.s
class WithDatabase(object):
    _database = attr.ib(converter=LockableDatabase)


class WithCursorTests(SyncTestCase):
    def test_recursive(self):
        """
        Trying to call a :py:`with_cursor` decorated function from another such
        function raises :py:`RecusiveTransaction`.
        """

        class Config(WithDatabase):
            @with_cursor
            def inner(self, cursor):
                pass

            @with_cursor
            def outer(self, cursor):
                self.inner()

        config = Config(sqlite3.connect(":memory:"))

        with ExpectedException(RecusiveTransaction, ".*when calling 'inner'.*"):
            config.outer()

    def test_excption_rollback(self):
        """
        Raising an exception from a :py:`with_cursor` decorated function rolls
        back the transaction.
        """

        class Config(WithDatabase):
            @with_cursor
            def f(self, cursor):
                cursor.execute("INSERT INTO [table] VALUES (1)")
                raise Exception()

        database = sqlite3.connect(":memory:")
        database.execute("CREATE TABLE [table] (value BOOL NOT NULL)")
        config = Config(database)
        with ExpectedException(Exception):
            config.f()

        self.assertThat(
            database.execute("SELECT * FROM [table]").fetchall(),
            Equals([]),
        )
