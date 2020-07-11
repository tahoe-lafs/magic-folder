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

from nacl.signing import (
    SigningKey,
)
from nacl.encoding import (
    RawEncoder,
    Base32Encoder,
)

from twisted.internet.endpoints import (
    serverFromString,
)
from twisted.python.filepath import (
    FilePath,
)

from .magicfolderdb import (
    with_cursor,
)
from .snapshot import (
    LocalAuthor,
)


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

_magicfolder_config_version = 1

_magicfolder_config_schema = """
CREATE TABLE version
(
    version INTEGER  -- contains one row, set to 1
);

CREATE TABLE author
(
    name          TEXT PRIMARY KEY,  -- UTF8 name of the author
    private_key   TEXT               -- base32 key in UTF8
);

CREATE TABLE poll_interval
(
    poll_interval INTEGER
);

CREATE TABLE local_snapshots
(
    id VARCHAR(256) PRIMARY KEY, -- identifier (hash of .. stuff)
    name TEXT,                   -- the (mangled) name in UTF8
    metadata TEXT,               -- arbitrary JSON metadata in UTF8
    content_path TEXT            -- where the content is sitting (path, UTF8)
);
"""
## XXX "parents_local" should be IDs of other local_snapshots, not
## sure how to do that w/o docs here


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

    config = GlobalConfigDatabase(
        database=connection,
        api_token_path=basedir.child("api_token"),
    )
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
    return GlobalConfigDatabase(
        database=connection,
        api_token_path=basedir.child("api_token"),
    )


@attr.s
class MagicFolderConfig(object):
    """
    Low-level access to a single magic-folder's configuration
    """
    name = attr.ib()
    database = attr.ib()  # sqlite3 Connection
    stash_path = attr.ib(validator=attr.validators.instance_of(FilePath))

    def __attrs_post_init__(self):
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("BEGIN IMMEDIATE TRANSACTION")
            cursor.execute("SELECT version FROM version");
            dbversion = cursor.fetchone()[0]
            if dbversion != _magicfolder_config_version:
                raise ConfigurationError(
                    "Magic Folder '{}' has unknown configuration database "
                    "version (wanted {}, got {})".format(
                        self.name,
                        _magicfolder_config_version,
                        dbversion,
                    )
                )

    @property
    def author(self):
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT name, private_key FROM author");
            name, keydata = cursor.fetchone()
            return LocalAuthor(
                name=name,
                signing_key=SigningKey(keydata, encoder=Base32Encoder),
            )


@attr.s
class GlobalConfigDatabase(object):
    """
    Low-level access to the global configuration database
    """
    database = attr.ib()  # sqlite3 Connection; needs validator
    api_token_path = attr.ib(validator=attr.validators.instance_of(FilePath))

    def __attrs_post_init__(self):
        self._api_token = None
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("BEGIN IMMEDIATE TRANSACTION")
            cursor.execute("SELECT version FROM version");
            dbversion = cursor.fetchone()[0]
            if dbversion != _global_config_version:
                raise ConfigurationError(
                    "Unknown configuration database version (wanted {}, got {})".format(
                        _global_config_version,
                        dbversion,
                    )
                )

    @property
    def api_token(self):
        """
        Current API token
        """
        if self._api_token is None:
            with self.api_token_path.open('rb') as f:
                self._api_token = f.read()
        return self._api_token

    def rotate_api_token(self):
        """
        Record a new random API token and then return it
        """
        self._api_token = urandom(32)
        with self.api_token_path.open('wb') as f:
            f.write(self._api_token)
        return self._api_token

    @property
    def api_endpoint(self):
        """
        The twisted server-string describing our API listener
        """
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
            cursor.execute("SELECT endpoint FROM api_endpoint")
            existing = cursor.fetchone()
            if existing:
                cursor.execute("UPDATE api_endpoint SET endpoint=?", (ep_string, ))
            else:
                cursor.execute("INSERT INTO api_endpoint VALUES (?)", (ep_string, ))

    def create_magic_folder(self, name, magic_path, state_path, author):
        """
        Add a new Magic Folder configuration.

        :param unicode name: a unique name for this magic-folder

        :param FilePath magic_path: the synchronized directory which
            must already exist.

        :param FilePath state_path: the configuration and state
            directory (which should not already exist)

        :param LocalAuthor author: the signer of snapshots created in
            this folder

        :returns: a MagicFolderConfig instance
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT name FROM magic_folders WHERE name=?", (name, ))
            if cursor.fetchone() is not None:
                raise ValueError(
                    "Already have a magic-folder named '{}'".format(name)
                )
        if not magic_path.exists():
            raise ValueError(
                "'{}' does not exist".format(magic_path.path)
            )
        if state_path.exists():
            raise ValueEerror(
                "'{}' already exists".format(state_path.path)
            )

        mkdir(state_path.path)
        stash_path = state_path.child("stash")
        mkdir(stash_path.path)
        db_path = state_path.child("state.sqlite")
        connection = sqlite3.connect(db_path.path)
        with connection:
            cursor = connection.cursor()
            cursor.execute("BEGIN IMMEDIATE TRANSACTION")
            cursor.executescript(_magicfolder_config_schema)
            connection.commit()
            cursor.execute(
                "INSERT INTO version (version) VALUES (?)",
                (_magicfolder_config_version, )
            )
            cursor.execute(
                "INSERT INTO author (name, private_key) VALUES (?, ?)",
                (author.name, author.signing_key.encode(Base32Encoder))
            )

        config = MagicFolderConfig(
            name=name,
            database=connection,
            stash_path=stash_path,
        )
        return config
