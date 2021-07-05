"""
Configuration and state database interaction.

See also docs/config.rst
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

__all__ = [
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
import hashlib
import time
from weakref import WeakValueDictionary

from hyperlink import (
    DecodedURL,
)

from functools import (
    partial,
)
from itertools import (
    chain,
)

from uuid import (
    UUID,
)

import attr
from attr.validators import (
    provides,
    instance_of,
)

import sqlite3

from nacl.signing import (
    SigningKey,
)
from nacl.encoding import (
    Base32Encoder,
)

from twisted.internet.endpoints import (
    serverFromString,
    clientFromString,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.compat import (
    nativeString,
)
from twisted.web import (
    http,
)

from zope.interface import (
    implementer,
    Interface,
)

from .snapshot import (
    LocalAuthor,
    LocalSnapshot,
)
from .common import (
    APIError,
    NoSuchMagicFolder,
    atomic_makedirs,
    valid_magic_folder_name,
)
from ._schema import (
    SchemaUpgrade,
    Schema,
)

# Export this here since GlobalConfigDatabase is what it's for.
from ._endpoint_parser import (
    endpoint_description_to_http_api_root,
)

from eliot import (
    ActionType,
    Field,
)

from .util.capabilities import is_readonly_directory_cap, is_directory_cap
from .util.database import with_cursor, LockableDatabase
from .util.eliotutil import (
    RELPATH,
    validateSetMembership,
)
from .util.file import (
    PathState,
    ns_to_seconds,
    seconds_to_ns,
)

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
            tahoe_node_directory TEXT,        -- path to our Tahoe-LAFS client state
            api_client_endpoint TEXT          -- Twisted client-string for our HTTP API
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
            poll_interval        INTEGER NOT NULL,  -- seconds
            scan_interval        INTEGER CHECK ([scan_interval] > 0) -- seconds
        )
        """,
        """
        CREATE TABLE [local_snapshots]
        (
            -- A random, unique identifier for this particular snapshot.
            [identifier]       TEXT PRIMARY KEY,

            -- The magicpath-mangled name of the file this snapshot is for,
            -- UTF-8-encoded.
            [name]             TEXT,

            -- A local filesystem path where the content can be found.
            [content_path]     TEXT
        )
        """,
        """
        CREATE TABLE [local_snapshot_metadata]
        (
            -- The identifier of the snapshot which this metadata row belongs to.
            [snapshot_identifier]   TEXT NOT NULL,

            -- The metadata key for this row.
            [key]                   TEXT NOT NULL,

            -- The handy SQLite3 "BLOB" storage class allows us to store a
            -- value of any supported type, even differing from row to row.
            [value]                 BLOB NOT NULL,

            -- The referenced local snapshot must exist.
            FOREIGN KEY([snapshot_identifier]) REFERENCES [local_snapshot]([identifier])
        )
        """,
        """
        CREATE TABLE [local_snapshot_parent]
        (
            -- The identifier of the snapshot to which this parent belongs.
            [snapshot_identifier]   TEXT NOT NULL,

            -- The index of this parent in the snapshot's ordered parent list.
            -- Offer marginal additional data integrity by requiring it to be
            -- 0 or greater.
            --
            -- It's possible parent order is irrelevant in which case we can
            -- eventually drop this column and all the associated logic to
            -- impose a particular order.
            [index]                 INTEGER CHECK ([index] >= 0) NOT NULL,

            -- If this parent is local only then its identifier is only
            -- meaningful for looking up local snapshots in our local
            -- database.  If it is not only local then its identifier is a
            -- capability refering to a remote snapshot which we may or may
            -- not have a local cache of.
            --
            -- Perhaps a better representation of this would involve two
            -- tables (or two columns in this table?).  It will be slightly
            -- easier to reason about once the implementation can actually
            -- create snapshots with remote parents.
            [local_only]            BOOL NOT NULL,

            -- The actual parent identifier.  This is either a reference to
            -- [local_snapshot]([identifier]) or a Tahoe-LAFS cap.
            [parent_identifier]     TEXT NOT NULL,

            -- The referenced local snapshot must exist.
            FOREIGN KEY([snapshot_identifier]) REFERENCES [local_snapshot]([identifier])
        )
        """,
        """
        -- This table represents the current state of the file on disk, as last known to us
        CREATE TABLE [current_snapshots]
        (
            [name]             TEXT PRIMARY KEY, -- mangled name in UTF-8
            [snapshot_cap]     TEXT,             -- Tahoe-LAFS URI that represents the most recent remote snapshot
                                                 -- associated with this file, either as downloaded from a peer
                                                 -- or uploaded from local changes
            [mtime_ns]         INTEGER NOT NULL, -- ctime of current snapshot
            [ctime_ns]         INTEGER NOT NULL, -- mtime of current snapshot
            [size]             INTEGER NOT NULL, -- size of current snapshot
            [last_updated_ns]  INTEGER NOT NULL  -- timestamp when last changed
        )
        """,
    ]),
])


## XXX "parents_local" should be IDs of other local_snapshots, not
## sure how to do that w/o docs here

DELETE_SNAPSHOTS = ActionType(
    u"config:state-db:delete-local-snapshot-entry",
    [RELPATH],
    [],
    u"Delete the row corresponding to the given path from the local snapshot table.",
)

FETCH_CURRENT_SNAPSHOTS_FROM_DB = ActionType(
    u"config:state-db:get-remote-snapshot-entry",
    [RELPATH],
    [],
    u"Delete the row corresponding to the given path from the local snapshot table.",
)
_INSERT_OR_UPDATE = Field.for_types(
    u"insert_or_update",
    [unicode],
    u"An indication of whether the record for this upload was new or an update to a previous entry.",
    validateSetMembership({u"insert", u"update"}),
)

STORE_OR_UPDATE_SNAPSHOTS = ActionType(
    u"config:state-db:update-snapshot-entry",
    [RELPATH],
    [_INSERT_OR_UPDATE],
    u"Persist local snapshot object of a relative path in the magic-folder db.",
)


class LocalSnapshotCollision(Exception):
    """
    An attempt was made to insert a local snapshot into the database but the
    snapshot's identifier was already associated with a snapshot in the
    database.
    """

@attr.s(auto_exc=True, frozen=True)
class LocalSnapshotMissingParent(Exception):
    """
    An attempt was made to store a local snapshot whose parents aren't in
    the local database.
    """
    parent_identifier = attr.ib(validator=attr.validators.instance_of(UUID))


@attr.s(auto_exc=True, frozen=True)
class RemoteSnapshotWithoutPathState(Exception):
    """
    An attempt was made to insert a remote snapshot into the database without
    corresponding path state (either provided, or already in the database).
    """

    folder_name = attr.ib(validator=attr.validators.instance_of(unicode))
    snapshot_name = attr.ib(validator=attr.validators.instance_of(unicode))


def create_global_configuration(basedir, api_endpoint_str, tahoe_node_directory,
                                api_client_endpoint_str):
    """
    Create a new global configuration in `basedir` (which must not yet exist).

    :param FilePath basedir: a non-existant directory

    :param unicode api_endpoint_str: the Twisted server endpoint string
        where we will listen for API requests.

    :param FilePath tahoe_node_directory: the directory our Tahoe LAFS
        client uses.

    :param unicode api_client_endpoint_str: the Twisted client endpoint
        string where our API can be contacted.

    :returns: a GlobalConfigDatabase instance
    """

    # our APIs insist on endpoint-strings being unicode, but Twisted
    # only accepts "str" .. so we have to convert on py2. When we
    # support python3 this check only needs to happen on py2
    if not isinstance(api_endpoint_str, unicode):
        raise ValueError(
            "'api_endpoint_str' must be unicode"
        )
    if not isinstance(api_client_endpoint_str, unicode):
        raise ValueError(
            "'api_client_endpoint_str' must be unicode"
        )
    api_endpoint_str = nativeString(api_endpoint_str)
    api_client_endpoint_str = nativeString(api_client_endpoint_str)
    # check that the endpoints are valid (will raise exception if not)
    _validate_listen_endpoint_str(api_endpoint_str)
    _validate_connect_endpoint_str(api_client_endpoint_str)

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
    connection = _upgraded(
        _global_config_schema,
        sqlite3.connect(db_fname.path),
    )
    with connection:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO config (api_endpoint, tahoe_node_directory, api_client_endpoint) VALUES (?, ?, ?)",
            (api_endpoint_str, tahoe_node_directory.path, api_client_endpoint_str)
        )

    config = GlobalConfigDatabase(
        basedir=basedir,
        database=connection,
        token_provider=FilesystemTokenProvider(
            basedir.child(b"api_token"),
        )
    )
    # make sure we have an API token
    config.rotate_api_token()
    return config


def create_testing_configuration(basedir, tahoe_node_directory):
    """
    Create a new global configuration that is in-memory and routes all
    API requests through http_root_resource.

    :param FilePath basedir: a directory where magic-folder state goes
        (only used if a magic-folder is created)

    :param FilePath tahoe_node_directory: the directory our Tahoe LAFS
        client uses.

    :returns: a GlobalConfigDatabase instance
    """
    # set up the configuration database
    connection = _upgraded(
        _global_config_schema,
        sqlite3.connect(":memory:"),
    )
    api_endpoint_str = "tcp:-1"
    api_client_endpoint_str = "tcp:127.0.0.1:-1"
    with connection:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO config (api_endpoint, tahoe_node_directory, api_client_endpoint) VALUES (?, ?, ?)",
            (api_endpoint_str, tahoe_node_directory.path, api_client_endpoint_str)
        )

    tokens = MemoryTokenProvider()

    config = GlobalConfigDatabase(
        basedir=basedir,
        database=connection,
        token_provider=tokens,
    )

    # make sure we have an API token
    config.rotate_api_token()
    return config


def load_global_configuration(basedir):
    """
    Load an existing configuration from `basedir`.

    :param FilePath basedir: an existing config directory

    :raise ValueError: If no database already exists beneath at ``basedir``.

    :raise DatabaseSchemaTooNew: If the database at ``basedir`` indicates a
        newer schema version than this software can handle.

    :returns: a GlobalConfigDatabase instance
    """
    db_fname = basedir.child("global.sqlite")

    # It would be nice to pass a URI-style connect string with ?mode=rwc
    # but this is unsupported until Python 3.4.
    if not db_fname.exists():
        raise ValueError(
            "'{}' doesn't exist.".format(db_fname.path),
        )

    connection = _upgraded(
        _global_config_schema,
        sqlite3.connect(db_fname.path),
    )
    return GlobalConfigDatabase(
        basedir=basedir,
        database=connection,
        token_provider=FilesystemTokenProvider(
            basedir.child("api_token"),
        )
    )


def _upgraded(schema, connection):
    """
    Return ``connection`` fully upgraded according to ``schema``.

    :param Schema schema: The schema to use to perform any necessary upgrades.

    :param sqlite3.Connection connection: The database connection to possibly
        upgrade.

    :return: ``connection`` after possibly upgrading its schema.
    """
    with connection:
        cursor = connection.cursor()
        cursor.execute("BEGIN IMMEDIATE TRANSACTION")
        schema.run_upgrades(cursor)
    return connection


@attr.s
class SQLite3DatabaseLocation(object):
    """
    A helper to allow a connection to a SQLite3 database on a filesystem or
    in-memory.
    """
    location = attr.ib()

    @classmethod
    def memory(cls):
        """
        Get an in-memory database location.
        """
        return cls(":memory:")

    def connect(self, *a, **kw):
        """
        Establish a new connection to the SQLite3 database at this location.

        :param *a: Additional positional arguments for ``sqlite3.connect``.
        :param *kw: Additional keyword arguments for ``sqlite3.connect``.

        :return: A new ``sqlite3.Connection``.
        """
        return sqlite3.connect(self.location, *a, **kw)


def _get_snapshots(cursor, name):
    """
    Load all of the snapshots associated with the given name.

    :param unicode name: The name to match.  See ``LocalSnapshot.name``.

    :return dict[unicode, unicode]: A mapping from unicode snapshot
        identifiers to unicode snapshot content path strings.
    """
    cursor.execute(
        """
        SELECT
            [identifier], [content_path]
        FROM
            [local_snapshots]
        WHERE
            [name] = ?
        """,
        (name,),
    )
    snapshots = cursor.fetchall()
    if len(snapshots) == 0:
        raise KeyError(name)
    return dict(snapshots)


def _get_metadata(cursor, name):
    """
    Load all of the metadata for all of the snapshots associated with the
    given name.

    :param unicode name: The name to match.  See ``LocalSnapshot.name``.

    :return dict[unicode, dict[unicode, unicode]]: A mapping from unicode
        snapshot identifiers to dicts of key/value metadata associated with
        that snapshot.
    """
    cursor.execute(
        """
        SELECT
            [snapshot_identifier], [key], [value]
        FROM
            [local_snapshot_metadata] AS [metadata], [local_snapshots]
        WHERE
            [metadata].[snapshot_identifier] = [local_snapshots].[identifier]
        AND
            [local_snapshots].[name] = ?
        """,
        (name,),
    )
    metadata_rows = cursor.fetchall()
    metadata = {}
    for (snapshot_identifier, key, value) in metadata_rows:
        metadata.setdefault(snapshot_identifier, {})[key] = value
    return metadata


def _get_parents(cursor, name):
    """
    Load all of the parent points for all of the snapshots associated with the
    given name.

    :param unicode name: The name to match.  See ``LocalSnapshot.name``.

    :return dict[unicode, [(int, bool, unicode)]]: A mapping from unicode
        snapshot identifiers to lists of associated parent pointer
        information.  Each list contains all of the associated snapshots
        parents.  The first element of each tuple in the list gives the parent
        index.  The second element is ``True`` if the parent is a local
        snapshot and ``False`` if it is a remote snapshot.  The third element
        is a unique identifier for that parent (either an opaque unicode
        string for local snapshots or a Tahoe-LAFS capability string for
        remote snapshots).
    """
    cursor.execute(
        """
        SELECT
            [snapshot_identifier], [index], [local_only], [parent_identifier]
        FROM
            [local_snapshot_parent] AS [parents], [local_snapshots]
        WHERE
            [parents].[snapshot_identifier] = [local_snapshots].[identifier]
        AND
            [local_snapshots].[name] = ?
        """,
        (name,),
    )
    parent_rows = cursor.fetchall()
    parents = {}
    for (snapshot_identifier, index, local_only, parent_identifier) in parent_rows:
        parents.setdefault(snapshot_identifier, []).append((
            index,
            local_only,
            parent_identifier,
        ))
    for parent in parents.values():
        parent.sort()

    return parents


def _find_leaf_snapshot(leaf_candidates, parents):
    """
    From a group of snapshots which are related to each other by parent/child
    edges in a tree structure, find the single leaf snapshot (the snapshot
    which is not the parent of any other snapshot).

    :param set[unicode] leaf_candidates: The set of identifiers of snapshots
        to consider.

    :param dict[unicode, [(int, bool, unicode)]] parents: Information about
        the parent/child relationships between the snapshots.  See
        ``_get_parents`` for details.

    :raise ValueError: If there is not exactly one leaf snapshot.

    :return unicode: The identifier of the leaf snapshot.
    """
    to_discard = set()
    for (ignored, local_only, parent_identifier) in chain.from_iterable(parents.values()):
        if local_only:
            # Allow a parent to be discarded more than once in case we have
            # snapshots that share a parent.
            to_discard.add(parent_identifier)

    remaining = leaf_candidates - to_discard
    if len(remaining) != 1:
        raise ValueError(
            "Database state inconsistent when loading local snapshot.  "
            "Leaf candidates: {!r}".format(
                remaining,
            ),
        )
    [leaf_identifier] = remaining
    return leaf_identifier


def _get_remote_parents(identifier, parents):
    """
    Get the identifiers for remote parents for the given snapshot.

    :param unicode identifier: The identifier of the snapshot about which to
        retrieve information.

    :param dict[unicode, [(int, bool, unicode)]] parents: Information about
        the parent/child relationships between the snapshots.  See
        ``_get_parents`` for details.

    :return [unicode]: Identifiers for the remote parents of the identified
        snapshot.
    """
    return list(
        parent_identifier
        for (ignored, only_local, parent_identifier)
        in parents.get(identifier, [])
        if not only_local
    )


def _get_local_parents(identifier, parents, construct_parent_snapshot):
    """
    Get the identifiers for local parents for the given snapshot.

    :param unicode identifier: The identifier of the snapshot about which to
        retrieve information.

    :param dict[unicode, [(int, bool, unicode)]] parents: Information about
        the parent/child relationships between the snapshots.  See
        ``_get_parents`` for details.

    :return [unicode]: Identifiers for the local parents of the identified
        snapshot.
    """
    return list(
        construct_parent_snapshot(parent_identifier)
        for (ignored, only_local, parent_identifier)
        in parents.get(identifier, [])
        if only_local
    )


def _construct_local_snapshot(identifier, name, author, content_paths, metadata, parents):
    """
    Instantiate a ``LocalSnapshot`` corresponding to the given identifier.

    :param unicode identifier: The identifier of the snapshot to instantiate.

    :param unicode name: The name to match.  See ``LocalSnapshot.name``.

    :param LocalAuthor author: The author associated with the snapshot.

    :param dict[unicode, unicode] content_paths: A mapping from snapshot
        identifiers to the filesystem location of the content for that
        snapshot.

    :param metadata: See ``_get_metadata``.

    :param parents: See ``_get_parents``.

    :return LocalSnapshot: The requested snapshot, populated with information
        from the given parameters, including fully initialized local parents.
    """
    return LocalSnapshot(
        identifier=UUID(hex=identifier),
        name=name,
        author=author,
        content_path=FilePath(content_paths[identifier]),
        metadata=metadata.get(identifier, {}),
        parents_remote=_get_remote_parents(identifier, parents),
        parents_local=_get_local_parents(
            identifier,
            parents,
            partial(
                _construct_local_snapshot,
                name=name,
                author=author,
                content_paths=content_paths,
                metadata=metadata,
                parents=parents,
            ),
        ),
    )


@attr.s
class MagicFolderConfig(object):
    """
    Low-level access to a single magic-folder's configuration
    """
    name = attr.ib()
    _database = attr.ib(converter=LockableDatabase)  # sqlite3 Connection
    _get_current_timestamp = attr.ib(default=time.time)

    @classmethod
    def initialize(
            cls,
            name,
            db_location,
            author,
            stash_path,
            collective_dircap,
            upload_dircap,
            magic_path,
            poll_interval,
            scan_interval,
    ):
        """
        Create the database state for a new Magic Folder and return a
        ``MagicFolderConfig`` representing it.

        :param unicode name: The human-facing name for this folder.

        :param SQLite3DatabaseLocation db_location: A SQLite3 location string
            to use to connect to the database for this folder.

        :param LocalAuthor author: The author to which all local changes will
            be attributed.

        :param FilePath stash_path: The filesystem location to which to write
            snapshot content before uploading it.

        :param unicode collective_dircap: A Tahoe-LAFS directory capability
            representing the Magic-Folder "collective" directory (where
            participant DMDs can be found).

        :param unicode upload_dircap: A Tahoe-LAFS read-write directory
            capability representing the DMD belonging to ``author``.

        :param FilePath magic_path: The local filesystem path where magic
            folder will read and write files belonging to this folder.

        :param int poll_interval: The interval, in seconds, on which to poll
            for remote changes (for download).

        :param int scan_interval: The interval, in seconds, on which to poll
            for local changes (for upload).

        :return: A new ``cls`` instance populated with the given
            configuration.
        """
        connection = _upgraded(
            _magicfolder_config_schema,
            db_location.connect(),
        )
        with connection:
            cursor = connection.cursor()
            cursor.execute("BEGIN IMMEDIATE TRANSACTION")
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
                    , scan_interval
                    )
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    author.name,
                    author.signing_key.encode(Base32Encoder),
                    stash_path.path,
                    collective_dircap,
                    upload_dircap,
                    magic_path.path,
                    poll_interval,
                    scan_interval,
                ),
            )
        return cls(name, connection)

    @property
    @with_cursor
    def author(self, cursor):
        cursor.execute("SELECT author_name, author_private_key FROM config")
        name, keydata = cursor.fetchone()
        return LocalAuthor(
            name=name,
            signing_key=SigningKey(keydata, encoder=Base32Encoder),
        )

    @property
    @with_cursor
    def stash_path(self, cursor):
        cursor.execute("SELECT stash_path FROM config")
        path_raw = cursor.fetchone()[0]
        return FilePath(path_raw)

    @with_cursor
    def get_local_snapshot(self, cursor, name):
        """
        return an instance of LocalSnapshot corresponding to
        the given name and author. Traversing the parents
        would give the entire history of local snapshots.

        :param unicode name: The name of the snapshot to find.  See
            ``LocalSnapshot.name``.

        :raise KeyError: If there is no matching snapshot for the given path.

        :returns: An instance of LocalSnapshot for the given magicpath.
        """
        # Read all the state for this name from the database.
        snapshots = _get_snapshots(cursor, name)
        metadata = _get_metadata(cursor, name)
        parents = _get_parents(cursor, name)

        # Turn it into the desired in-memory representation.
        leaf_identifier = _find_leaf_snapshot(set(snapshots), parents)
        return _construct_local_snapshot(
            leaf_identifier,
            name,
            self.author,
            snapshots,
            metadata,
            parents,
        )

    @with_cursor
    def store_local_snapshot(self, cursor, snapshot):
        """
        Store or update the given local snapshot.

        :param LocalSnapshot snapshot: The snapshot to store.
        """
        # Ensure that the local parent snapshots are already in the database.
        for parent in snapshot.parents_local:
            cursor.execute(
                """
                SELECT
                    count(*) from [local_snapshots]
                WHERE
                    identifier= ?
                """,
                (unicode(parent.identifier),),
            )
            count = cursor.fetchone()
            if count[0] != 1:
                raise LocalSnapshotMissingParent(parent.identifier)

        try:
            # Create the primary row.
            cursor.execute(
                """
                INSERT INTO
                    [local_snapshots] ([identifier], [name], [content_path])
                VALUES
                    (?, ?, ?)
                """,
                (unicode(snapshot.identifier), snapshot.name, snapshot.content_path.asTextMode("utf-8").path),
            )
        except sqlite3.IntegrityError:
            # The UNIQUE constraint on `identifier` failed - which *should*
            # mean we already have a row representing this snapshot.
            raise LocalSnapshotCollision(snapshot.identifier)

        # Associate all of the metadata with it.
        cursor.executemany(
            """
            INSERT INTO
                [local_snapshot_metadata] ([snapshot_identifier], [key], [value])
            VALUES
                (?, ?, ?)
            """,
            list(
                (unicode(snapshot.identifier), k, v)
                for (k, v)
                in snapshot.metadata.items()
            ),
        )
        # Associate all of the parents with it.  First remote, then local.
        #
        # XXX What is the relative ordering of parents_local and
        # parents_remote?  It's not preserved in `LocalSnapshot`.  Since local
        # parents turn into remote parents I guess I'll put remote parents
        # first here.  That way as local snapshots disappear they'll disappear
        # from the middle and appear at the end of the remote snapshots list,
        # also in the middle?
        cursor.executemany(
            """
            INSERT INTO
                [local_snapshot_parent] (
                    [snapshot_identifier],
                    [index],
                    [local_only],
                    [parent_identifier]
                )
            VALUES
                (?, ?, ?, ?)
            """,
            [
                (unicode(snapshot.identifier), index, local_only, parent_identifier)
                for (index, (local_only, parent_identifier)) in enumerate(
                    chain(
                        (
                            (False, parent_identifier)
                            for parent_identifier in snapshot.parents_remote
                        ),
                        (
                            (True, unicode(parent.identifier))
                            for parent in snapshot.parents_local
                        ),
                    )
                )
            ],
        )

    @with_cursor
    def get_all_localsnapshot_paths(self, cursor):
        """
        Retrieve a set of all relpaths of files that have had an entry in magic folder db
        (i.e. that have been downloaded at least once).
        """
        cursor.execute("SELECT [name] FROM [local_snapshots]")
        rows = cursor.fetchall()
        return set(r[0] for r in rows)

    @with_cursor
    def delete_localsnapshot(self, cursor, name):
        """
        remove the row corresponding to the given name from the local_snapshots table

        :param unicode name: The name to match.  See ``LocalSnapshot.name``.
        """
        action = DELETE_SNAPSHOTS(
            relpath=name,
        )
        with action:
            cursor.execute("DELETE FROM [local_snapshots]"
                           " WHERE [name]=?",
                           (name,))


    @with_cursor
    def store_uploaded_snapshot(self, cursor, name, remote_snapshot):
        """
        Store the remote snapshot cap of a snapshot that we uploaded.

        This assumes that the path state was already recoreded when we stored
        the corresponding local snapshot.

        :param unicode name: The name to match.  See ``LocalSnapshot.name``.
        :param RemoteSnapshot remote_snapshot: The snapshot to store.

        :raises RemoteSnapshotWithoutPathState:
            if there is not already path state in the database for the given
            name.
        """
        # TODO: We should consider merging this with delete_localsnapshot
        snapshot_cap = remote_snapshot.capability
        action = STORE_OR_UPDATE_SNAPSHOTS(
            relpath=name,
        )
        now_ns = seconds_to_ns(self._get_current_timestamp())
        with action:
            cursor.execute(
                """
                UPDATE
                    current_snapshots
                SET
                    snapshot_cap=?, last_updated_ns=?
                WHERE
                    [name]=?
                """,
                (snapshot_cap, now_ns, name),
            )
            if cursor.rowcount != 1:
                raise RemoteSnapshotWithoutPathState(
                    folder_name=self.name, snapshot_name=name
                )
            action.add_success_fields(insert_or_update="update")

    @with_cursor
    def store_downloaded_snapshot(self, cursor, name, remote_snapshot, path_state):
        """
        Store the remote snapshot cap for a file that we downloaded, and
        the corresponding path state of the written file.

        :param unicode name: The name to match.  See ``LocalSnapshot.name``.
        :param PathState path_state: The state of the path to record.
        :param RemoteSnapshot remote_snapshot: The snapshot to store.
        """
        snapshot_cap = remote_snapshot.capability
        action = STORE_OR_UPDATE_SNAPSHOTS(
            relpath=name,
        )
        now_ns = seconds_to_ns(self._get_current_timestamp())
        with action:
            try:
                cursor.execute(
                    "INSERT INTO current_snapshots (name, snapshot_cap, mtime_ns, ctime_ns, size, last_updated_ns)"
                    " VALUES (?,?,?,?,?,?)",
                    (name, snapshot_cap, path_state.mtime_ns, path_state.ctime_ns, path_state.size, now_ns),
                )
                action.add_success_fields(insert_or_update="insert")
            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                cursor.execute(
                    "UPDATE current_snapshots"
                    " SET snapshot_cap=?, mtime_ns=?, ctime_ns=?, size=?, last_updated_ns=?"
                    " WHERE [name]=?",
                    (snapshot_cap, path_state.mtime_ns, path_state.ctime_ns, path_state.size, now_ns, name),
                )
                action.add_success_fields(insert_or_update="update")

    @with_cursor
    def store_currentsnapshot_state(self, cursor, name, path_state):
        """
        Store or update the path state of the given name.

        :param unicode name: The name to match.  See ``LocalSnapshot.name``.
        :param PathState path_state: The path state to store.
        """
        action = STORE_OR_UPDATE_SNAPSHOTS(
            relpath=name,
        )
        now_ns = seconds_to_ns(self._get_current_timestamp())
        with action:
            try:
                cursor.execute(
                    "INSERT INTO current_snapshots (name, mtime_ns, ctime_ns, size, last_updated_ns)"
                    " VALUES (?,?,?,?,?)",
                    (name, path_state.mtime_ns, path_state.ctime_ns, path_state.size, now_ns),
                )
                action.add_success_fields(insert_or_update="insert")
            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                cursor.execute(
                    "UPDATE current_snapshots"
                    " SET mtime_ns=?, ctime_ns=?, size=?, last_updated_ns=?"
                    " WHERE [name]=?",
                    (path_state.mtime_ns, path_state.ctime_ns, path_state.size, now_ns, name),
                )
                action.add_success_fields(insert_or_update="update")

    @with_cursor
    def get_all_snapshot_paths(self, cursor):
        """
        Retrieve a set of all relpaths of files that have had an entry in magic folder db
        (i.e. that have been downloaded at least once).
        """
        cursor.execute("SELECT [name] FROM [current_snapshots]")
        rows = cursor.fetchall()
        return set(r[0] for r in rows)

    @with_cursor
    def get_recent_remotesnapshot_paths(self, cursor, n):
        """
        Retrieve a set of the ``n`` most-recent relpaths of files that
        have a remote representation.

        :returns: a list of 3-tuples (relpath, unix-timestamp, last-updated)
        """
        cursor.execute(
            """
            SELECT
                name, mtime_ns, last_updated_ns
            FROM
                [current_snapshots]
            ORDER BY
                mtime_ns DESC
            LIMIT
                30
            """
        )
        rows = cursor.fetchall()
        return [(r[0], ns_to_seconds(r[1]), ns_to_seconds(r[2])) for r in rows]

    @with_cursor
    def get_remotesnapshot(self, cursor, name):
        """
        return the cap that represents the latest remote snapshot that
        the client has recorded in the db.

        :param unicode name: The name to match.  See ``LocalSnapshot.name``.

        :raise KeyError: If no snapshot exists for the given name.

        :returns: A byte string that represents the RemoteSnapshot cap.
        """
        action = FETCH_CURRENT_SNAPSHOTS_FROM_DB(
            relpath=name,
        )
        with action:
            cursor.execute("SELECT snapshot_cap FROM current_snapshots"
                           " WHERE [name]=?",
                           (name,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                return row[0].encode("utf-8")
            raise KeyError(name)

    @with_cursor
    def get_currentsnapshot_pathstate(self, cursor, name):
        """
        return the timestamp of the latest remote snapshot that the client
        has recorded in the db.

        :param unicode name: The name to match.  See ``LocalSnapshot.name``.

        :raise KeyError: If no snapshot exists for the given name.

        :returns int: the timestamp of the remotesnapshot
        """
        action = FETCH_CURRENT_SNAPSHOTS_FROM_DB(
            relpath=name,
        )
        with action:
            cursor.execute(
                "SELECT mtime_ns, ctime_ns, size FROM current_snapshots"
                " WHERE [name]=?",
                (name,),
            )
            row = cursor.fetchone()
            if row:
                return PathState(mtime_ns=row[0], ctime_ns=row[1], size=row[2])
            raise KeyError(name)

    @with_cursor
    def get_all_current_snapshot_pathstates(self, cursor):
        """
        Return the PathState for every file we have in our
        'current_snapshot' table.

        :returns Iterable[(unicode, PathState)]: an iterable of
            3-tuples of (name, PathState instance, last-update), one for each file
            (ordered by last-updated timestamp)
        """
        cursor.execute(
            """
            SELECT
                name, mtime_ns, ctime_ns, size, last_updated_ns
            FROM
                current_snapshots
            ORDER BY
                last_updated_ns DESC
            """,
        )

        return [
            (
                row[0],  # name
                PathState(mtime_ns=row[1], ctime_ns=row[2], size=row[3]),
                row[4],  # last_updated_ns
            )
            for row in cursor.fetchall()
        ]

    @property
    @with_cursor
    def magic_path(self, cursor):
        cursor.execute("SELECT magic_directory FROM config")
        path_raw = cursor.fetchone()[0]
        return FilePath(path_raw)

    @property
    @with_cursor
    def collective_dircap(self, cursor):
        cursor.execute("SELECT collective_dircap FROM config")
        return cursor.fetchone()[0].encode("utf8")

    @collective_dircap.setter
    @with_cursor
    def collective_dircap(self, cursor, dircap):
        """
        This is for use by tests that need a non-admin collective.
        """
        if not is_directory_cap(dircap):
            raise AssertionError(
                "Collective dirnode was {!r}, must be a directory node.".format(
                    dircap,
                )
            )
        cursor.execute("UPDATE [config] SET collective_dircap=?", (dircap,))

    @property
    @with_cursor
    def upload_dircap(self, cursor):
        cursor.execute("SELECT upload_dircap FROM config")
        return cursor.fetchone()[0].encode("utf8")

    @property
    @with_cursor
    def poll_interval(self, cursor):
        cursor.execute("SELECT poll_interval FROM config")
        return int(cursor.fetchone()[0])

    @property
    @with_cursor
    def scan_interval(self, cursor):
        cursor.execute("SELECT scan_interval FROM config")
        return cursor.fetchone()[0]

    def is_admin(self):
        """
        :returns: True if this device can administer this folder. That is,
            if the collective capability we have is mutable.
        """
        # check if this folder has a writable collective dircap
        return not is_readonly_directory_cap(self.collective_dircap)


class ITokenProvider(Interface):
    """
    How the configuration retrieves and sets API access tokens.
    """

    def get():
        """
        Retrieve the current token.

        :returns: url-safe base64 encoded token (which decodes to
            32-bytes of random binary data).
        """

    def rotate():
        """
        Change the current token to a new random one.
        """


@attr.s
@implementer(ITokenProvider)
class FilesystemTokenProvider(object):
    """
    Keep a token on the filesystem
    """
    api_token_path = attr.ib(validator=instance_of(FilePath))
    _api_token = attr.ib(default=None)

    def get(self):
        """
        Retrieve the current token.
        """
        if self._api_token is None:
            try:
                self._load_token()
            except OSError:
                self.rotate()
                self._load_token()
        return self._api_token

    def rotate(self):
        """
        Change the current token to a new random one.
        """
        # this goes directly into Web headers, so we use the same
        # encoding as Tahoe uses.
        self._api_token = urlsafe_b64encode(urandom(32))
        with self.api_token_path.open('wb') as f:
            f.write(self._api_token)
        return self._api_token

    def _load_token(self):
        """
        Internal helper. Reads the token file into _api_token
        """
        with self.api_token_path.open('rb') as f:
            self._api_token = f.read()


@attr.s
@implementer(ITokenProvider)
class MemoryTokenProvider(object):
    """
    Keep an in-memory token.
    """
    _api_token = attr.ib(default=None)

    def get(self):
        """
        Retrieve the current token.
        """
        if self._api_token is None:
            self.rotate()
        return self._api_token

    def rotate(self):
        """
        Change the current token to a new random one.
        """
        # this goes directly into Web headers, so we use the same
        # encoding as Tahoe uses.
        self._api_token = urlsafe_b64encode(urandom(32))
        return self._api_token


@attr.s
class GlobalConfigDatabase(object):
    """
    Low-level access to the global configuration database

    :attr WeakValueDictionary[unicode, MagicFolderConfig] _folder_config_cache:
        This is a cache of `MagicFolderConfig` instances keyed by the folder name.
        We do this so we have only a single :py:`sqlite3.Connection` to the
        underlying state db.
    """
    # where magic-folder state goes
    basedir = attr.ib(validator=instance_of(FilePath))
    database = attr.ib(validator=instance_of(sqlite3.Connection))
    _token_provider = attr.ib(validator=provides(ITokenProvider))
    _folder_config_cache = attr.ib(init=False, factory=WeakValueDictionary)

    @property
    def api_token(self):
        """
        Current API token
        """
        return self._token_provider.get()

    def rotate_api_token(self):
        """
        Record a new random API token and then return it
        """
        return self._token_provider.rotate()

    @property
    def api_endpoint(self):
        """
        The twisted server-string describing our API listener
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT api_endpoint FROM config")
            return cursor.fetchone()[0].encode("utf8")

    @api_endpoint.setter
    def api_endpoint(self, ep_string):
        _validate_listen_endpoint_str(ep_string)
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("UPDATE config SET api_endpoint=?", (ep_string, ))

    @property
    def api_client_endpoint(self):
        """
        The twisted client-string describing our API listener
        """
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT api_client_endpoint FROM config")
            return cursor.fetchone()[0].encode("utf8")

    @api_client_endpoint.setter
    def api_client_endpoint(self, ep_string):
        _validate_connect_endpoint_str(ep_string)
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("UPDATE config SET api_client_endpoint=?", (ep_string, ))

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
        with node_dir.child("node.url").open("r") as f:
            return DecodedURL.from_text(f.read().strip().decode("utf8"))

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

        :raises NoSuchMagicFolder: if there is no such Magic Folder
        """
        # We can't use `if name in cache: return cache[name]` here
        # as the cache is a WeakValueDictionary, and the value might
        # die between the check and the return.
        try:
            return self._folder_config_cache[name]
        except KeyError:
            # Not in the cache, continue with creating a new MagicFolderConfig instance.
            pass
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT name, location FROM magic_folders WHERE name=?", (name, ))
            data = cursor.fetchone()
            if data is None:
                raise NoSuchMagicFolder(name)
            name, location = data
            connection = _upgraded(
                _magicfolder_config_schema,
                sqlite3.connect(FilePath(location).child("state.sqlite").path),
            )

            config = MagicFolderConfig(
                name=name,
                database=connection,
            )
            self._folder_config_cache[name] = config
            return config

    def _get_state_path(self, name):
        """
        :param unicode name: the name of a magic-folder (doesn't have to
            exist yet)

        :returns: the directory-name to contain the state of a magic-folder.
            This directory will not exist and will be a sub-directory of the
            config location.
        """
        h = hashlib.sha256()
        h.update(name.encode("utf8"))
        hashed_name = urlsafe_b64encode(h.digest()).decode('ascii')
        return self.basedir.child(u"folder-{}".format(hashed_name))

    def remove_magic_folder(self, name):
        """
        Remove and purge all information about a magic-folder. Note that
        if the collective_dircap is a write-capability it will be
        impossible to administer that folder any longer.

        :param unicode name: the folder to remove

        :returns: a list of (path, Exception) pairs if any directory cleanup
            failed (after removing config from the database).
        """
        folder_config = self.get_magic_folder(name)
        cleanup_dirs = [
            folder_config.stash_path,
        ]
        # Close the per-folder state database. Since `get_magic_folder` caches
        # its return value, this should be the only instance. We need to close
        # it explicitly since otherwise we can't delete it below on windows.
        folder_config.database.close()
        # we remove things from the database first and then give
        # best-effort attempt to remove stuff from the
        # filesystem. First confirm we have this folder and its
        # state-path.
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT location FROM magic_folders WHERE name=?", (name, ))
            folders = cursor.fetchall()
            if not folders:
                raise ValueError(
                    "No magic-folder named '{}'".format(name)
                )
            (state_path, ) = folders[0]
        cleanup_dirs.append(FilePath(state_path))

        with self.database:
            cursor = self.database.cursor()
            cursor.execute("DELETE FROM magic_folders WHERE name=?", (name, ))

        # Now that we have removed the entry for the folder from the database,
        # we remove the `MagicFolderConfig` instance from the cache. Since it
        # isn't in the database, a call to `get_magic_folder` can't cause a
        # second connection to open.
        # We can't depend on this being cleared automatically, since `MagicFolder`
        # has circular references.
        self._folder_config_cache.pop(name)

        # clean-up directories, in order
        failed_cleanups = []
        for clean in cleanup_dirs:
            try:
                clean.remove()
            except Exception as e:
                failed_cleanups.append((clean.path, e))
        return failed_cleanups

    def create_magic_folder(
        self,
        name,
        magic_path,
        author,
        collective_dircap,
        upload_dircap,
        poll_interval,
        scan_interval,
    ):
        """
        Add a new Magic Folder configuration.

        :param unicode name: a unique name for this magic-folder

        :param FilePath magic_path: the synchronized directory which
            must already exist.

        :param LocalAuthor author: the signer of snapshots created in
            this folder

        :param unicode collective_dircap: the read-capability of the
            directory defining the magic-folder.

        :param unicode upload_dircap: the write-capability of the
            directory we upload data into.

        :param int poll_interval: how often to scan for remote changes
            (in seconds).

        :param Optional[int] scan_interval: how often to scan for local changes
            (in seconds). ``None`` means to not scan periodically.

        :returns: a MagicFolderConfig instance
        """
        valid_magic_folder_name(name)
        if not isinstance(poll_interval, int) or poll_interval <= 0:
            raise APIError(
                code=http.BAD_REQUEST,
                reason="Poll interval must be a positive integer (not '{!r}').".format(
                    poll_interval
                ),
            )
        if scan_interval is not None and (
            not isinstance(scan_interval, int) or scan_interval <= 0
        ):
            raise APIError(
                code=http.BAD_REQUEST,
                reason="Scan interval must be a positive integer or null (not '{!r}').".format(
                    scan_interval
                ),
            )
        with self.database:
            cursor = self.database.cursor()
            cursor.execute("SELECT name FROM magic_folders WHERE name=?", (name, ))
            if len(cursor.fetchall()):
                raise APIError(
                    code=http.CONFLICT,
                    reason="Already have a magic-folder named '{}'".format(name)
                )
        if not magic_path.asBytesMode("utf-8").exists():
            raise APIError(
                code=http.BAD_REQUEST,
                reason="'{}' does not exist".format(magic_path.path)
            )
        state_path = self._get_state_path(name).asTextMode("utf-8")
        if state_path.asBytesMode("utf-8").exists():
            raise APIError(
                code=http.INTERNAL_SERVER_ERROR,
                reason="magic-folder state directory '{}' already exists".format(state_path.path)
            )

        stash_path = state_path.child(u"stash").asTextMode("utf-8")
        with atomic_makedirs(state_path), atomic_makedirs(stash_path):
            db_path = state_path.child("state.sqlite")
            mfc = MagicFolderConfig.initialize(
                name,
                SQLite3DatabaseLocation(db_path.path),
                author,
                stash_path.asTextMode("utf-8"),
                collective_dircap,
                upload_dircap,
                magic_path.asTextMode("utf-8"),
                poll_interval,
                scan_interval,
            )
            # add to the global config
            with self.database:
                cursor = self.database.cursor()
                cursor.execute("BEGIN IMMEDIATE TRANSACTION")
                cursor.execute(
                    "INSERT INTO magic_folders VALUES (?, ?)",
                    (name, state_path.asTextMode("utf-8").path)
                )

        return mfc


def _validate_listen_endpoint_str(ep_string):
    """
    confirm we have a valid endpoint-string
    """
    from twisted.internet import reactor
    # XXX so, having the reactor here sucks...but not a lot of options
    # since serverFromString is the only way to validate an
    # endpoint-string
    serverFromString(reactor, nativeString(ep_string))


def _validate_connect_endpoint_str(ep_string):
    """
    confirm we have a valid client-type endpoint-string
    """
    from twisted.internet import reactor
    # XXX so, having the reactor here sucks...but not a lot of options
    # since serverFromString is the only way to validate an
    # endpoint-string
    clientFromString(reactor, nativeString(ep_string))
