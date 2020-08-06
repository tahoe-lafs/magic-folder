"""
Configuration and state database interaction.

See also docs/config.rst
"""

from __future__ import (
    unicode_literals,
)

__all__ = [
    "ConfigurationError",
    "MagicFolderConfig",
    "GlobalConfigDatabase",

    "endpoint_description_to_http_api_root",
    "create_global_configuration",
    "load_global_configuration",
]

from os import (
    urandom,
)
from base64 import (
    urlsafe_b64encode,
)

from functools import (
    wraps,
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
    LocalSnapshot,
)
from .common import (
    atomic_makedirs,
)

from ._schema import (
    SchemaUpgrade,
    Schema,
)

# Export this here since GlobalConfigDatabase is what it's for.
from ._endpoint_parser import (
    endpoint_description_to_http_api_root,
)


class ConfigurationError(Exception):
    """
    The configuration is unusable for some reason
    """


class SnapshotNotFound(Exception):
    """
    No snapshot for a particular requested path was found.
    """


_global_config_schema = Schema([
    SchemaUpgrade([
        """
        CREATE TABLE magic_folders
        (
            name          TEXT PRIMARY KEY,  -- UTF8 name of this folder
            location      TEXT               -- UTF8 path to this folder's configuration/state
        );
        """,
        """
        CREATE TABLE config
        (
            api_endpoint TEXT,                -- Twisted server-string for our HTTP API
            tahoe_node_directory TEXT         -- path to our Tahoe-LAFS client state
        );
        """,
    ])
])

_magicfolder_config_schema = Schema([
    SchemaUpgrade([
        """
        CREATE TABLE config
        (
            author_name          TEXT PRIMARY KEY,  -- UTF8 name of the author
            author_private_key   TEXT,              -- base32 key in UTF8
            stash_path           TEXT,              -- local path for stash-data
            collective_dircap    TEXT,              -- read-capability-string
            upload_dircap        TEXT,              -- write-capability-string
            magic_directory      TEXT,              -- local path of sync'd directory
            poll_interval        INTEGER            -- seconds
        )
        """,
        """
        CREATE TABLE local_snapshots
        (
            path          TEXT PRIMARY KEY,  -- the (mangled) name in UTF8
            snapshot_blob BLOB               -- a JSON blob representing the snapshot instance
        )
        """,
    ]),
])

## XXX "parents_local" should be IDs of other local_snapshots, not
## sure how to do that w/o docs here


def create_global_configuration(basedir, api_endpoint, tahoe_node_directory):
    """
    Create a new global configuration in `basedir` (which must not yet exist).

    :param FilePath basedir: a non-existant directory

    :param unicode api_endpoint: the Twisted server endpoint string
        where we will listen for API requests.

    :param FilePath tahoe_node_directory: the directory our Tahoe LAFS
        client uses.

    :returns: a GlobalConfigDatabase instance
    """
    # note that we put *bytes* in .child() calls after this so we
    # don't convert again..
    basedir = basedir.asBytesMode("utf8")

    try:
        basedir.makedirs()
    except OSError as e:
        raise ValueError(
            "'{}' already exists: {}".format(basedir.path, e)
        )

    # explain what is in this directory
    with basedir.child(b"README").open("wb") as f:
        f.write(
            u"This is a Magic Folder daemon configuration\n"
            u"\n"
            u"To find out more you can run a command like:\n"
            u"\n"
            u"    magic-folder --config {} --help\n"
            u"\n".format(basedir.asTextMode("utf8").path).encode("utf8")
        )

    # set up the configuration database
    db_fname = basedir.child(b"global.sqlite")
    connection = sqlite3.connect(db_fname.path)
    with connection:
        cursor = connection.cursor()
        cursor.execute("BEGIN IMMEDIATE TRANSACTION")
        _global_config_schema.run_upgrades(cursor)
        cursor.execute(
            "INSERT INTO config (api_endpoint, tahoe_node_directory) VALUES (?, ?)",
            (api_endpoint, tahoe_node_directory.path)
        )

    config = GlobalConfigDatabase(
        database=connection,
        api_token_path=basedir.child(b"api_token"),
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


# XXX: with_cursor lacks unit tests, see:
#      https://github.com/LeastAuthority/magic-folder/issues/173
def with_cursor(f):
    """
    Decorate a function so it is automatically passed a cursor with an active
    transaction as the first positional argument.  If the function returns
    normally then the transaction will be committed.  Otherwise, the
    transaction will be rolled back.
    """
    @wraps(f)
    def with_cursor(self, *a, **kw):
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("BEGIN IMMEDIATE TRANSACTION")
            return f(self, cursor, *a, **kw)
    return with_cursor


@attr.s
class MagicFolderConfig(object):
    """
    Low-level access to a single magic-folder's configuration
    """
    name = attr.ib()
    database = attr.ib()  # sqlite3 Connection

    @property
    @with_cursor
    def author(self, cursor):
        cursor.execute("SELECT author_name, author_private_key FROM config");
        name, keydata = cursor.fetchone()
        return LocalAuthor(
            name=name,
            signing_key=SigningKey(keydata, encoder=Base32Encoder),
        )

    @property
    @with_cursor
    def stash_path(self, cursor):
        cursor.execute("SELECT stash_path FROM config");
        path_raw = cursor.fetchone()[0]
        return FilePath(path_raw)

    @with_cursor
    def get_local_snapshot(self, cursor, name, author):
        """
        return an instance of LocalSnapshot corresponding to
        the given name and author. Traversing the parents
        would give the entire history of local snapshots.

        :param unicode name: magicpath that represents the relative path of the file.

        :param author: an instance of LocalAuthor

        :raise SnapshotNotFound: If there is no matching snapshot for the
            given path.

        :returns: An instance of LocalSnapshot for the given magicpath.
        """
        cursor.execute("SELECT snapshot_blob FROM local_snapshots"
                       " WHERE path=?",
                       (name,))
        row = cursor.fetchone()
        if row:
            return LocalSnapshot.from_json(row[0], author)
        raise SnapshotNotFound(name)

    @with_cursor
    def store_local_snapshot(self, cursor, snapshot):
        """
        Store or update the given local snapshot.

        :param LocalSnapshot snapshot: The snapshot to store.
        """
        # insert a new row or update an existing row with the new blob.
        try:
            cursor.execute(
                """
                INSERT INTO
                    [local_snapshots] ([path], [snapshot_blob])
                VALUES
                    (?, ?)
                """,
                (snapshot.name, snapshot.to_json()),
            )
        except sqlite3.IntegrityError:
            # There is already a row with the given path.  Once we can depend
            # on a newer SQLite3 we can use an UPSERT instead.  Meanwhile,
            cursor.execute(
                """
                UPDATE
                    [local_snapshots]
                SET
                    [snapshot_blob] = ?
                WHERE
                    [path] = ?
                """,
                (snapshot.to_json(), snapshot.name),
            )

    @with_cursor
    def get_all_localsnapshot_paths(self, cursor):
        """
        Retrieve a set of all relpaths of files that have had an entry in magic folder db
        (i.e. that have been downloaded at least once).
        """
        cursor.execute("SELECT [path] FROM [local_snapshots]")
        rows = cursor.fetchall()
        return set(r[0] for r in rows)


@attr.s
class GlobalConfigDatabase(object):
    """
    Low-level access to the global configuration database
    """
    database = attr.ib()  # sqlite3 Connection; needs validator
    api_token_path = attr.ib(validator=attr.validators.instance_of(FilePath))
    _api_token = attr.ib(default=None)

    @with_cursor
    def __attrs_post_init__(self, cursor):
        _global_config_schema.run_upgrades(cursor)

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
            cursor.execute("SELECT tahoe_node_directory FROM config")
            node_dir = FilePath(cursor.fetchone()[0])
        with node_dir.child("node.url").open("rt") as f:
            return f.read().strip()

    @property
    def tahoe_node_directory(self):
        """
        The directory containing or Tahoe-LAFS client's configuration.
        :returns: FilePath
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT tahoe_node_directory FROM config")
            node_dir = FilePath(cursor.fetchone()[0])
        return node_dir

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

        stash_path = state_path.child("stash")
        with atomic_makedirs(state_path), atomic_makedirs(stash_path):
            db_path = state_path.child("state.sqlite")
            connection = sqlite3.connect(db_path.path)
            with connection:
                cursor = connection.cursor()
                cursor.execute("BEGIN IMMEDIATE TRANSACTION")
                _magicfolder_config_schema.run_upgrades(cursor)

                # default configuration values
                cursor.execute(
                    """
                    INSERT INTO
                        [config]
                        ( author_name
                        , author_private_key
                        , stash_path
                        , collective_dircap
                        , upload_dircap
                        , magic_directory
                        , poll_interval
                        )
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        author.name,
                        author.signing_key.encode(Base32Encoder),
                        stash_path.path,
                        collective_dircap,
                        upload_dircap,
                        magic_path.path,
                        poll_interval,
                    ),
                )

            # add to the global config
            with self.database:
                cursor = self.database.cursor()
                cursor.execute(
                    "INSERT INTO magic_folders VALUES (?, ?)",
                    (name, state_path.path)
                )

        return MagicFolderConfig(
            name=name,
            database=connection,
        )
