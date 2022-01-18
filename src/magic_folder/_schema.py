# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
This module defines the database schema used by the model interface.

:var int MAXIMUM_UPGRADES: The maximum number of upgrades that are allowed in
    a single schema.  This is set to an arbitrary value which should allow for
    quite a lot of schema changes but which still offers us a finite bound.
"""

import attr

MAXIMUM_UPGRADES = 1000

@attr.s(auto_exc=True)
class DatabaseSchemaTooNew(Exception):
    """
    The schema in the database is newer than the Python schema representation.

    This version of the software cannot use this version of the database.
    """
    software_version = attr.ib()
    database_version = attr.ib()

    def __str__(self):
        return repr(self)



def change_user_version(cursor, get_new_version):
    """
    Increment the **user version** field in a database using the given cursor.

    :param (int -> int) get_new_version: Given the current version, return the
        new version to write to the database.  The new version must fit in a
        signed 4 byte integer.

    :see: https://www.sqlite.org/pragma.html#pragma_user_version
    """
    def get_version(cursor):
        cursor.execute("PRAGMA [user_version]")
        [(version,)] = cursor.fetchall()
        return version

    version = get_version(cursor)
    new_version = get_new_version(version)
    # You cannot use bind arguments with PRAGMA. :/
    cursor.execute("PRAGMA [user_version] = {}".format(new_version))

    stored_version = get_version(cursor)
    if stored_version != new_version:
        # If it was the wrong type or if it was out of bounds then the pragma
        # will silently fail, possibly setting the stored version to 0.
        raise ValueError(
            "Failed to record new user_version {!r} (stored {!r})".format(
                new_version,
                stored_version,
            ),
        )


@attr.s(frozen=True)
class SchemaUpgrade(object):
    """
    An upgrade from one schema version to the next.

    :ivar list[unicode] statements: A list of statements to execute to
        complete this upgrade.
    """
    statements = attr.ib(validator=attr.validators.instance_of(list))

    def run(self, cursor):
        """
        Execute this upgrade against the given cursor.

        This method does no transaction management.  It uses the cursor in
        whatever state it is in.

        :param cursor: A DB-API cursor to use to run the SQL.
        """
        for statement in self.statements:
            cursor.execute(statement)
        change_user_version(cursor, lambda old: old + 1)

@attr.s
class Schema(object):
    """
    The schema for a single database.

    A ``Schema`` allows software to be written and maintained against a single
    most up-to-date schema version.  ``Schema`` provides helpers to open a
    database and require that it have that (most up-to-date) schema version,
    possibly upgrading it in the process.

    The empty schema, versioned as 0, is the beginning of history for all
    schemas.  From there, upgrades can be applied which will increment the
    schema version and make some changes to the schema.

    :ivar list[SchemaUpgrade] upgrades: A list of schema upgrades.  Each
        element upgrades the schema *from* the schema version with a number
        corresponding to the index of that element.  For example, the first
        element in the list is the upgrade to run against the empty version 0
        of the schema.
    """
    upgrades = attr.ib()

    @property
    def version(self):
        """
        Get the version number which identifies this particular schema.
        """
        return len(self.upgrades)

    @upgrades.validator
    def _validate_upgrades(self, attribute, value):
        if len(value) > MAXIMUM_UPGRADES:
            # If you hit this case, congratulations on your epic schema.  Your
            # prize is that you get to implement some kind of schema
            # consolidation mechanism.
            raise ValueError(
                "Schema has {} upgrades, greater than maximum allowed {}".format(
                    len(value),
                    MAXIMUM_UPGRADES,
                ),
            )

    def get_version(self, cursor):
        """
        Read the current schema version from the database using the given cursor.

        This method does no transaction management.  It uses the cursor in
        whatever state it is in.
        """
        cursor.execute("PRAGMA [user_version]")
        [(actual_version,)] = cursor.fetchall()
        return actual_version

    def get_upgrades(self, from_version):
        """
        Generate ``SchemaUpgrade`` instances to alter a schema at ``from_version``
        so that it matches the latest version.

        :param int from_version: The version of the schema which may require
            upgrade.
        """
        return self.upgrades[from_version:]

    def run_upgrades(self, cursor):
        """
        Run all known, applicable upgrades (in increasing order) using the given
        cursor.

        An upgrade is applicable if it is for a newer schema version than is
        currently present in the database.

        This method does no transaction management.  It uses the cursor in
        whatever state it is in.

        :param list[unicode] upgrades: The SQL statements to apply for the
            upgrade.

        :param cursor: A DB-API cursor to use to run the SQL.
        """
        database_version = self.get_version(cursor)
        if database_version > self.version:
            raise DatabaseSchemaTooNew(
                software_version=self.version,
                database_version=database_version,
            )

        upgrades = self.get_upgrades(database_version)
        for upgrade in upgrades:
            upgrade.run(cursor)
