"""
Configuration and state database interaction.

See also docs/config.rst
"""

from os import (
    mkdir,
    urandom,
)

import attr

import sqlite3

from twisted.internet.endpoints import (
    serverFromString,
)

from .magicfolderdb import with_cursor


class ConfigurationError(Exception):
    """
    The configuration is unusable for some reason
    """


_global_config_version = 1

_global_config_schema = """
CREATE TABLE version
(
    version INTEGER  -- contains one row, set to 1
);

CREATE TABLE magic_folders
(
    name          TEXT PRIMARY KEY,  -- UTF8 name of this folder
    location      TEXT               -- UTF8 path to this folder's configuration/state
);

CREATE TABLE api_endpoint
(
    endpoint TEXT
);
"""


def create_global_configuration(basedir, api_endpoint):
    """
    Create a new global configuration in `basedir` (which must not yet exist).

    :param FilePath basedir: a non-existant directory

    :returns: a GlobalConfigDatabase instance
    """
    if basedir.exists():
        raise ValueError(
            "'{}' already exists".format(basedir.path)
        )
    mkdir(basedir.path)

    # set up the configuration database
    db_fname = basedir.child("global.sqlite")
    connection = sqlite3.connect(db_fname.path)
    with connection:
        cursor = connection.cursor()
        cursor.execute("BEGIN IMMEDIATE TRANSACTION")
        cursor.executescript(_global_config_schema)
        connection.commit()
        cursor.execute("INSERT INTO version (version) VALUES (?)", (_global_config_version, ))

    # create the first API token
    with basedir.child("api_token").open("w") as f:
        f.write(urandom(32))

    config = GlobalConfigDatabase(database=connection)
    config.api_endpoint = api_endpoint
    return config


def load_global_configuration(basedir):
    """
    Load an existing configuration from `basedir`.

    :param FilePath basedir: an existing config directory

    :returns: a GlobalConfigDatabase instance
    """
    if not basedir.exists():
        raise ValueError(
            "'{}' doesn't exist".format(basedir.path)
        )
    db_fname = basedir.child("global.sqlite")
    connection = sqlite3.connect(db_fname.path)
    return GlobalConfigDatabase(database=connection)


@attr.s
class GlobalConfigDatabase(object):
    """
    Low-level access to the global configuration database
    """
    database = attr.ib()  # sqlite3 Connection; needs validator

    def __attrs_post_init__(self):
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("BEGIN IMMEDIATE TRANSACTION")
            cursor.execute("SELECT version FROM version");
            dbversion = cursor.fetchone()[0]
            if dbversion != _global_config_version:
                raise ConfigurationError(
                    "Unknown configuration database version (wanted {}, got {})".format(
                        self.version,
                        dbversion,
                    )
                )

    @property
    def api_endpoint(self):
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT endpoint FROM api_endpoint")
            return cursor.fetchone()[0]

    @api_endpoint.setter
    def api_endpoint(self, ep_string):
        # confirm we have a valid endpoint-string
        from twisted.internet import reactor  # uhm...
        # XXX so, having the reactor here sucks. But if we pass in an
        # IStreamServerEndpoint instead, how can we turn that back
        # into an endpoint-string?
        serverFromString(reactor, ep_string)

        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT endpoint from api_endpoint")
            existing = cursor.fetchone()
            if existing:
                cursor.execute("UPDATE api_endpoint SET endpoint=?", (ep_string, ))
            else:
                cursor.execute("INSERT INTO api_endpoint VALUES (?)", (ep_string, ))
