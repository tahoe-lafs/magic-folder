# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
This module defines the database schema used by the model interface.

:var int MAXIMUM_UPGRADES: The maximum number of upgrades that are allowed in
    a single schema.
"""

from __future__ import (
    unicode_literals,
    absolute_import,
)

import attr

_CREATE_VERSION = (
    """
    CREATE TABLE IF NOT EXISTS [schema-version] AS SELECT 0 AS [version]
    """
)

_INCREMENT_VERSION = (
    """
    UPDATE [schema-version]
    SET [version] = [version] + 1
    """
)

_READ_VERSION = (
    """
    SELECT [version] FROM [schema-version]
    """
)

MAXIMUM_UPGRADES = 1000

@attr.s(frozen=True)
class SchemaUpgrade(object):
    """
    An upgrade from one schema version to the next.

    :ivar list[unicode] statements: A list of statements to execute to
        complete this upgrade.
    """
    statements = attr.ib(validator=attr.validators.instance_of(list))

    def run(self, cursor):
        for statement in self.statements:
            cursor.execute(statement)
        cursor.execute(_INCREMENT_VERSION)

@attr.s
class Schema(object):
    """
    The schema for a single database.

    :ivar list[SchemaUpgrade] upgrades: A list of schema upgrades.  Each
        element upgrades the schema *from* the schema version with a number
        corresponding to the index of that element.
    """
    upgrades = attr.ib()

    @upgrades.validator
    def _validate_upgrades(self, attribute, value):
        if len(value) > MAXIMUM_UPGRADES:
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
        cursor.execute(_CREATE_VERSION)
        cursor.execute(_READ_VERSION)
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
        current_version = self.get_version(cursor)
        upgrades = self.get_upgrades(current_version)
        for upgrade in upgrades:
            upgrade.run(cursor)
