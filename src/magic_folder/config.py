"""
Configuration and state database interaction.

See also docs/config.rst
"""

from os import (
    urandom,
)
from base64 import (
    urlsafe_b64encode,
)

import attr

import sqlite3

from nacl.signing import (
    SigningKey,
)
from nacl.encoding import (
    Base32Encoder,
)

from twisted.internet.endpoints import (
    serverFromString,
)
from twisted.python.filepath import (
    FilePath,
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

CREATE TABLE config
(
    api_endpoint TEXT,                -- Twisted server-string for our HTTP API
    tahoe_client_url TEXT            -- HTTP URL of our Tahoe-LAFS client
);
"""

_magicfolder_config_version = 1

_magicfolder_config_schema = """
CREATE TABLE version
(
    version INTEGER  -- contains one row, set to 1
);

CREATE TABLE stash
(
    path TEXT    -- the path to our stash-directory
);

CREATE TABLE config
(
    author_name          TEXT PRIMARY KEY,  -- UTF8 name of the author
    author_private_key   TEXT,              -- base32 key in UTF8
    stash_path           TEXT,              -- local path for stash-data
    collective_dircap    TEXT,              -- read-capability-string
    upload_dircap        TEXT,              -- write-capability-string
    magic_directory      TEXT,              -- local path of sync'd directory
    poll_interval        INTEGER            -- seconds
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


def create_global_configuration(basedir, api_endpoint, tahoe_client_url):
    """
    Create a new global configuration in `basedir` (which must not yet exist).

    :param FilePath basedir: a non-existant directory

    :param unicode api_endpoint: the Twisted server endpoint string
        where we will listen for API requests.

    :param unicode tahoe_client_url: the Twisted client endpoint
        string where we will contact our Tahoe LAFS client WebUI.

    :returns: a GlobalConfigDatabase instance
    """
    try:
        basedir.makedirs()
    except OSError:
        raise ValueError(
            "'{}' already exists".format(basedir.path)
        )

    # explain what is in this directory
    with basedir.child("README").open("w") as f:
        f.write(
            "This is a Magic Folder daemon configuration\n"
            "\n"
            "To find out more you can run a command like:\n"
            "\n"
            "    magic-folder --config {} --help\n"
            "\n".format(basedir.path)
        )

    # set up the configuration database
    db_fname = basedir.child("global.sqlite")
    connection = sqlite3.connect(db_fname.path)
    with connection:
        cursor = connection.cursor()
        cursor.execute("BEGIN IMMEDIATE TRANSACTION")
        cursor.executescript(_global_config_schema)
        connection.commit()
        cursor.execute(
            "INSERT INTO version (version) VALUES (?)",
            (_global_config_version, )
        )
        cursor.execute(
            "INSERT INTO config (api_endpoint, tahoe_client_url) VALUES (?, ?)",
            (api_endpoint, tahoe_client_url)
        )

    config = GlobalConfigDatabase(
        database=connection,
        api_token_path=basedir.child("api_token"),
    )
    # make sure we have an API token
    config.rotate_api_token()
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
            cursor.execute("SELECT author_name, author_private_key FROM config");
            name, keydata = cursor.fetchone()
            return LocalAuthor(
                name=name,
                signing_key=SigningKey(keydata, encoder=Base32Encoder),
            )

    @property
    def stash_path(self):
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT stash_path FROM config");
            path_raw = cursor.fetchone()[0]
            return FilePath(path_raw)


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
        # this goes directly into Web headers, so we use the same
        # encoding as Tahoe uses.
        self._api_token = urlsafe_b64encode(urandom(32))
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
            cursor.execute("SELECT api_endpoint FROM config")
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
            cursor.execute("UPDATE config SET api_endpoint=?", (ep_string, ))

    @property
    def tahoe_client_url(self):
        """
        The twisted client-string describing how we will connect to the
        Tahoe LAFS client we will use.
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT tahoe_client_url FROM config")
            return cursor.fetchone()[0]

    @tahoe_client_url.setter
    def tahoe_client_url(self, url):
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("UPDATE config SET tahoe_client_url=?", (url, ))

    def list_magic_folders(self):
        """
        Return a generator that yields the names of all magic-folders
        configured. Use `get_magic_folder` to retrieve the
        configuration for a speicific folder.
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT name FROM magic_folders")
            for row in cursor.fetchall():
                yield row[0]

    def get_magic_folder(self, name):
        """
        Find the config for an existing Magic Folder.

        :param unicode name: the unique name of the magic-folder to find

        :returns: a MagicFolderConfig instance

        :raises: ValueError if there is no such Magic Folder
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT name, location FROM magic_folders WHERE name=?", (name, ))
            data = cursor.fetchone()
            if data is None:
                raise ValueError(
                    "No Magic Folder named '{}'".format(name)
                )
            name, location = data
            connection = sqlite3.connect(FilePath(location).child("state.sqlite").path)
            config = MagicFolderConfig(
                name=name,
                database=connection,
            )
            return config

    def create_magic_folder(self, name, magic_path, state_path, author,
                            collective_dircap, upload_dircap, poll_interval):
        """
        Add a new Magic Folder configuration.

        :param unicode name: a unique name for this magic-folder

        :param FilePath magic_path: the synchronized directory which
            must already exist.

        :param FilePath state_path: the configuration and state
            directory (which should not already exist)

        :param LocalAuthor author: the signer of snapshots created in
            this folder

        :param unicode collective_dircap: the read-capability of the
            directory defining the magic-folder.

        :param unicode upload_dircap: the write-capability of the
            directory we upload data into.

        :param FilePath magic_directory: local path to the folder we
            synchronize for this magic-folder.

        :returns: a MagicFolderConfig instance
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT name FROM magic_folders WHERE name=?", (name, ))
            if len(cursor.fetchall()):
                raise ValueError(
                    "Already have a magic-folder named '{}'".format(name)
                )
        if not magic_path.exists():
            raise ValueError(
                "'{}' does not exist".format(magic_path.path)
            )
        if state_path.exists():
            raise ValueError(
                "'{}' already exists".format(state_path.path)
            )

        from contextlib import contextmanager
        @contextmanager
        def atomic_makedirs(path):
            path.makedirs()
            try:
                yield path
            except Exception:
                # on error, clean up our directory
                path.remove()
                # ...and pass on the error
                raise

        stash_path = state_path.child("stash")
        with atomic_makedirs(state_path), atomic_makedirs(stash_path):
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
                # default configuration values
                cursor.execute(
                    "INSERT INTO CONFIG (author_name, author_private_key, stash_path, collective_dircap, upload_dircap, magic_directory, poll_interval) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        author.name,
                        author.signing_key.encode(Base32Encoder),
                        stash_path.path,
                        collective_dircap,
                        upload_dircap,
                        magic_path.path,
                        poll_interval,
                    )
                )
                cursor.execute(
                    "INSERT INTO stash (path) VALUES (?)",
                    (stash_path.path, )
                )

            config = MagicFolderConfig(
                name=name,
                database=connection,
            )

            # add to the global config
            with self.database:
                cursor = self.database.cursor()
                cursor.execute(
                    "INSERT INTO magic_folders VALUES (?, ?)",
                    (name, state_path.path)
                )
        return config
