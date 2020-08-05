# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``_zkapauthorizer.schema``.
"""

from __future__ import (
    unicode_literals,
    absolute_import,
)

from testtools import (
    TestCase,
    ExpectedException,
)
from testtools.matchers import (
    Equals,
)

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    integers,
    data,
    lists,
)

from sqlite3 import (
    connect,
)

from .._schema import (
    MAXIMUM_UPGRADES,
    SchemaUpgrade,
    Schema,
)

class SchemaTests(TestCase):
    """
    Tests for ``Schema``.
    """
    @given(
        integers(
            min_value=MAXIMUM_UPGRADES + 1,
            max_value=MAXIMUM_UPGRADES * 100,
        ),
    )
    def test_too_many_upgrades(self, num_upgrades):
        """
        ``Schema`` raises `ValueError`` if initialized with a schema with more
        than ``MAXIMUM_UPGRADES`` upgrades.
        """
        with ExpectedException(ValueError):
            Schema(
                upgrades=[SchemaUpgrade(["SELECT 1"])] * num_upgrades,
            )

    @given(
        integers(min_value=0, max_value=MAXIMUM_UPGRADES),
    )
    def test_get_version(self, num_upgrades):
        """
        ``Schema.get_version`` returns the version number to which the schema has
        been upgraded.
        """
        upgrades = [SchemaUpgrade(["SELECT 1"])] * num_upgrades
        schema = Schema(upgrades=upgrades)

        db = connect(":memory:")
        cursor = db.cursor()
        schema.run_upgrades(cursor)
        self.assertThat(
            schema.get_version(cursor),
            Equals(num_upgrades),
        )

    @given(
        lists(
            integers(
                min_value=-2 ** 63,
                max_value=2 ** 63,
            ),
            unique=True,
            min_size=1,
            max_size=MAXIMUM_UPGRADES,
        ),
        data(),
    )
    def test_upgrades_run(self, values, data):
        """
        ``Schema.run_upgrades`` executes all of the statements from the given
        ``SchemaUpgrade`` instances.
        """
        # Pick a version at which to start the database.
        current_version = data.draw(
            integers(min_value=0, max_value=len(values)),
        )

        upgrades = list(
            # Interpolating into SQL here ... bad form but I don't want to
            # hand-code a bunch of unique SQL statements for this test.  A
            # schema upgrade would normally not have a variable in it like
            # this.
            SchemaUpgrade(["INSERT INTO [a] ([b]) VALUES ({})".format(value)])
            for value
            in values
        )

        schema = Schema(upgrades=upgrades)
        db = connect(":memory:")
        cursor = db.cursor()

        # Create the table we're going to mess with.
        cursor.execute("CREATE TABLE [a] ([b] INTEGER)")

        # Force version schema creation.
        schema.get_version(cursor)

        # Fast-forward to the state we're going to pretend the database is at.
        cursor.execute(
            "UPDATE [schema-version] SET [version] = ?",
            (current_version,),
        )

        # Run whatever upgrades remain appropriate.
        schema.run_upgrades(cursor)

        cursor.execute("SELECT [b] FROM [a]")
        selected_values = list(b for (b,) in cursor.fetchall())

        self.assertThat(
            selected_values,
            Equals(values[current_version:]),
        )
