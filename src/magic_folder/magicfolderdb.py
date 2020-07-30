from __future__ import print_function

import sys
from collections import namedtuple

from allmydata.util.dbutil import get_db, DBError
from .util.eliotutil import (
    RELPATH,
    VERSION,
    LAST_UPLOADED_URI,
    LAST_DOWNLOADED_URI,
    LAST_DOWNLOADED_TIMESTAMP,
    PATHINFO,
    validateSetMembership,
    validateInstanceOf,
)

from .snapshot import (
    LocalSnapshot,
)

from eliot import (
    Field,
    ActionType,
)

from functools import (
    wraps,
)

PathEntry = namedtuple('PathEntry', 'size mtime_ns ctime_ns version last_uploaded_uri '
                                    'last_downloaded_uri last_downloaded_timestamp')

PATHENTRY = Field(
    u"pathentry",
    lambda v: None if v is None else {
        "size": v.size,
        "mtime_ns": v.mtime_ns,
        "ctime_ns": v.ctime_ns,
        "version": v.version,
        "last_uploaded_uri": v.last_uploaded_uri,
        "last_downloaded_uri": v.last_downloaded_uri,
        "last_downloaded_timestamp": v.last_downloaded_timestamp,
    },
    u"The local database state of a file.",
    validateInstanceOf((type(None), PathEntry)),
)

_INSERT_OR_UPDATE = Field.for_types(
    u"insert_or_update",
    [unicode],
    u"An indication of whether the record for this upload was new or an update to a previous entry.",
    validateSetMembership({u"insert", u"update"}),
)

UPDATE_ENTRY = ActionType(
    u"magic-folder-db:update-entry",
    [RELPATH, VERSION, LAST_UPLOADED_URI, LAST_DOWNLOADED_URI, LAST_DOWNLOADED_TIMESTAMP, PATHINFO],
    [_INSERT_OR_UPDATE],
    u"Record some metadata about a relative path in the magic-folder.",
)

STORE_OR_UPDATE_SNAPSHOTS = ActionType(
    u"magic-folder-db:update-snapshot-entry",
    [RELPATH],
    [_INSERT_OR_UPDATE],
    u"Persist local snapshot object of a relative path in the magic-folder db.",
)

DELETE_SNAPSHOTS = ActionType(
    u"magic-folder-db:delete-local-snapshot-entry",
    [RELPATH],
    [],
    u"Delete the row corresponding to the given path from the local snapshot table.",
)

# magic-folder db schema version 1
SCHEMA_v1 = """
CREATE TABLE version
(
 version INTEGER  -- contains one row, set to 1
);

CREATE TABLE local_files
(
 path                VARCHAR(1024) PRIMARY KEY,   -- UTF-8 filename relative to local magic folder dir
 size                INTEGER,                     -- ST_SIZE, or NULL if the file has been deleted
 mtime_ns            INTEGER,                     -- ST_MTIME in nanoseconds
 ctime_ns            INTEGER,                     -- ST_CTIME in nanoseconds
 version             INTEGER,
 last_uploaded_uri   VARCHAR(256),                -- URI:CHK:...
 last_downloaded_uri VARCHAR(256),                -- URI:CHK:...
 last_downloaded_timestamp TIMESTAMP
);

CREATE TABLE local_snapshots
(
 path               TEXT PRIMARY KEY,             -- UTF-8 relative filepath that the snapshot represents
 snapshot_blob      BLOB                          -- a JSON blob representing the snapshot instance
);
"""


def get_magicfolderdb(dbfile, stderr=sys.stderr,
                      create_version=(SCHEMA_v1, 1), just_create=False):
    # Open or create the given backupdb file. The parent directory must
    # exist.
    try:
        (sqlite3, db) = get_db(dbfile, stderr, create_version,
                               just_create=just_create, dbname="magicfolderdb")
        if create_version[1] in (1, 2):
            return MagicFolderDB(sqlite3, db)
        else:
            print("invalid magicfolderdb schema version specified", file=stderr)
            return None
    except DBError as e:
        print(e, file=stderr)
        return None

class LocalPath(object):
    @classmethod
    def fromrow(self, row):
        p = LocalPath()
        p.relpath_u = row[0]
        p.entry = PathEntry(*row[1:])
        return p

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
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute("BEGIN IMMEDIATE TRANSACTION")
            return f(self, cursor, *a, **kw)
    return with_cursor


class MagicFolderDB(object):
    VERSION = 1

    def __init__(self, sqlite_module, connection):
        self.sqlite_module = sqlite_module
        self.connection = connection
        self.cursor = connection.cursor()

    def close(self):
        self.connection.close()

    # for use in tests
    def _clear(self, table):
        """
        delete all the rows in a specified table.

        :param unicode table: table that needs to be cleared
        """
        c = self.cursor
        c.execute("DELETE FROM {}".format(table))

    def get_db_entry(self, relpath_u):
        """
        Retrieve the entry in the database for a given path, or return None
        if there is no such entry.
        """
        c = self.cursor
        c.execute("SELECT size, mtime_ns, ctime_ns, version, last_uploaded_uri,"
                  "       last_downloaded_uri, last_downloaded_timestamp"
                  " FROM local_files"
                  " WHERE path=?",
                  (relpath_u,))
        row = self.cursor.fetchone()
        if not row:
            return None
        else:
            (size, mtime_ns, ctime_ns, version, last_uploaded_uri,
             last_downloaded_uri, last_downloaded_timestamp) = row
            return PathEntry(size=size, mtime_ns=mtime_ns, ctime_ns=ctime_ns, version=version,
                             last_uploaded_uri=last_uploaded_uri,
                             last_downloaded_uri=last_downloaded_uri,
                             last_downloaded_timestamp=last_downloaded_timestamp)

    def get_direct_children(self, relpath_u):
        """
        Given the relative path to a directory, return ``LocalPath`` instances
        representing all direct children of that directory.
        """
        # It would be great to not be interpolating data into query
        # statements.  However, query parameters are not supported in the
        # position where we need them.
        sqlitesafe_relpath_u = relpath_u.replace(u"'", u"''")
        statement = (
            """
            SELECT
                path, size, mtime_ns, ctime_ns, version, last_uploaded_uri,
                last_downloaded_uri, last_downloaded_timestamp
            FROM
                local_files
            WHERE
                -- The "_" used here ensures there is at least one character
                -- after the /.  This prevents matching the path itself.
                path LIKE '{path}/_%' AND

                -- The "_" used here serves a similar purpose.  This allows
                -- matching directory children but avoids matching their
                -- children.
                path NOT LIKE '{path}/_%/_%'
            """
        ).format(path=sqlitesafe_relpath_u)

        self.cursor.execute(statement)
        rows = self.cursor.fetchall()
        return list(
            LocalPath.fromrow(row)
            for row
            in rows
        )

    def _get_all_relpaths_from(self, tablename):
        """
        :param unicode tablename: table name to fetch the relpaths from.

        :returns: A set of all the relpaths in the given table.
        """
        self.cursor.execute("SELECT path FROM {}".format(tablename))
        rows = self.cursor.fetchall()
        return set([r[0] for r in rows])

    def get_all_relpaths(self):
        """
        Retrieve a set of all relpaths of files that have had an entry in magic folder db
        (i.e. that have been downloaded at least once).
        """
        return self._get_all_relpaths_from("local_files")

    def did_upload_version(self, relpath_u, version, last_uploaded_uri, last_downloaded_uri, last_downloaded_timestamp, pathinfo):
        action = UPDATE_ENTRY(
            relpath=relpath_u,
            version=version,
            last_uploaded_uri=last_uploaded_uri,
            last_downloaded_uri=last_downloaded_uri,
            last_downloaded_timestamp=last_downloaded_timestamp,
            pathinfo=pathinfo,
        )
        with action:
            try:
                self.cursor.execute("INSERT INTO local_files VALUES (?,?,?,?,?,?,?,?)",
                                    (relpath_u, pathinfo.size, pathinfo.mtime_ns, pathinfo.ctime_ns,
                                     version, last_uploaded_uri, last_downloaded_uri,
                                     last_downloaded_timestamp))
                action.add_success_fields(insert_or_update=u"insert")
            except (self.sqlite_module.IntegrityError, self.sqlite_module.OperationalError):
                self.cursor.execute("UPDATE local_files"
                                    " SET size=?, mtime_ns=?, ctime_ns=?, version=?, last_uploaded_uri=?,"
                                    "     last_downloaded_uri=?, last_downloaded_timestamp=?"
                                    " WHERE path=?",
                                    (pathinfo.size, pathinfo.mtime_ns, pathinfo.ctime_ns, version,
                                     last_uploaded_uri, last_downloaded_uri, last_downloaded_timestamp,
                                     relpath_u))
                action.add_success_fields(insert_or_update=u"update")
            self.connection.commit()

    @with_cursor
    def store_local_snapshot(self, cursor, snapshot):
        """
        Store or update the given Local Snapshot for the
        given the magicpath of the file (mangled file path).

        :param str snapshot: A LocalSnapshot instance
        """
        action = STORE_OR_UPDATE_SNAPSHOTS(
            relpath=snapshot.name,
        )
        with action:
            serialized_snapshot = snapshot.to_json()
            try:
                cursor.execute("INSERT INTO local_snapshots VALUES (?,?)",
                               (snapshot.name, serialized_snapshot))
                action.add_success_fields(insert_or_update=u"insert")
            except (self.sqlite_module.IntegrityError, self.sqlite_module.OperationalError):
                cursor.execute("UPDATE local_snapshots"
                               " SET snapshot_blob=?"
                               " WHERE path=?",
                               (serialized_snapshot, snapshot.name))
                action.add_success_fields(insert_or_update=u"update")
            self.connection.commit()

    @with_cursor
    def get_local_snapshot(self, cursor, name, author):
        """
        return an instance of LocalSnapshot corresponding to
        the given name and author. Traversing the parents
        would give the entire history of local snapshots.

        :param str name: magicpath that represents the relative path of the file.

        :param author: an instance of LocalAuthor

        :returns: An instance of LocalSnapshot for the given magicpath.
        """
        cursor.execute("SELECT snapshot_blob FROM local_snapshots"
                       " WHERE path=?",
                       (name,))
        row = cursor.fetchone()
        if not row:
            return None
        else:
            return LocalSnapshot.from_json(row[0], author)

    def get_all_localsnapshot_paths(self):
        """
        Retrieve a set of all relpaths of files that have had an entry in magic folder db
        (i.e. that have been downloaded at least once).
        """
        return self._get_all_relpaths_from("local_snapshots")

    def _clear_snapshot_table(self):
        return self._clear("local_snapshots")

    @with_cursor
    def delete_local_snapshot(self, cursor, name):
        """
        Delete the row corresponding to the given ''name''.

        :param str name: magicpath that represents the relative path of the file.
        """
        action = DELETE_SNAPSHOTS(
            relpath=name,
        )
        with action:
            cursor.execute("DELETE FROM local_snapshots"
                           " WHERE path=?",
                           (name,))
            self.connection.commit()
