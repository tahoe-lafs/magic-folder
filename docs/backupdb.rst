﻿.. -*- coding: utf-8-with-signature -*-

==================
The Tahoe BackupDB
==================

1.  `Overview`_
2.  `Schema`_
3.  `Upload Operation`_
4.  `Directory Operations`_

Overview
========
To speed up backup operations, Tahoe maintains a small database known as the
"backupdb". This is used to avoid re-uploading files which have already been
uploaded recently.

This database lives in ``~/.tahoe/private/backupdb.sqlite``, and is a SQLite
single-file database. It is used by the "``tahoe backup``" command. In the
future, it may optionally be used by other commands such as "``tahoe cp``".

The purpose of this database is twofold: to manage the file-to-cap
translation (the "upload" step) and the directory-to-cap translation (the
"mkdir-immutable" step).

The overall goal of optimizing backup is to reduce the work required when the
source disk has not changed (much) since the last backup. In the ideal case,
running "``tahoe backup``" twice in a row, with no intervening changes to the
disk, will not require any network traffic. Minimal changes to the source
disk should result in minimal traffic.

This database is optional. If it is deleted, the worst effect is that a
subsequent backup operation may use more effort (network bandwidth, CPU
cycles, and disk IO) than it would have without the backupdb.

The database uses sqlite3, which is included as part of the standard Python
library with Python 2.5 and later. For Python 2.4, Tahoe will try to install the
"pysqlite" package at build-time, but this will succeed only if sqlite3 with
development headers is already installed.  On Debian and Debian derivatives
you can install the "python-pysqlite2" package (which, despite the name,
actually provides sqlite3 rather than sqlite2). On old distributions such
as Debian etch (4.0 "oldstable") or Ubuntu Edgy (6.10) the "python-pysqlite2"
package won't work, but the "sqlite3-dev" package will.

Schema
======

The database contains the following tables::

  CREATE TABLE version
  (
   version integer  # contains one row, set to 1
  );
  
  CREATE TABLE local_files
  (
   path  varchar(1024),  PRIMARY KEY -- index, this is an absolute UTF-8-encoded local filename
   size  integer,         -- os.stat(fn)[stat.ST_SIZE]
   mtime number,          -- os.stat(fn)[stat.ST_MTIME]
   ctime number,          -- os.stat(fn)[stat.ST_CTIME]
   fileid integer
  );
  
  CREATE TABLE caps
  (
   fileid integer PRIMARY KEY AUTOINCREMENT,
   filecap varchar(256) UNIQUE    -- URI:CHK:...
  );
  
  CREATE TABLE last_upload
  (
   fileid INTEGER PRIMARY KEY,
   last_uploaded TIMESTAMP,
   last_checked TIMESTAMP
  );
  
  CREATE TABLE directories
  (
   dirhash varchar(256) PRIMARY KEY,
   dircap varchar(256),
   last_uploaded TIMESTAMP,
   last_checked TIMESTAMP
  );

Upload Operation
================

The upload process starts with a pathname (like ``~/.emacs``) and wants to end up
with a file-cap (like ``URI:CHK:...``).

The first step is to convert the path to an absolute form
(``/home/warner/.emacs``) and do a lookup in the local_files table. If the path
is not present in this table, the file must be uploaded. The upload process
is:

1. record the file's size, ctime (which is the directory-entry change time or
   file creation time depending on OS) and modification time

2. upload the file into the grid, obtaining an immutable file read-cap

3. add an entry to the 'caps' table, with the read-cap, to get a fileid

4. add an entry to the 'last_upload' table, with the current time

5. add an entry to the 'local_files' table, with the fileid, the path,
   and the local file's size/ctime/mtime

If the path *is* present in 'local_files', the easy-to-compute identifying
information is compared: file size and ctime/mtime. If these differ, the file
must be uploaded. The row is removed from the local_files table, and the
upload process above is followed.

If the path is present but ctime or mtime differs, the file may have changed.
If the size differs, then the file has certainly changed. At this point, a
future version of the "backup" command might hash the file and look for a
match in an as-yet-defined table, in the hopes that the file has simply been
moved from somewhere else on the disk. This enhancement requires changes to
the Tahoe upload API before it can be significantly more efficient than
simply handing the file to Tahoe and relying upon the normal convergence to
notice the similarity.

If ctime, mtime, or size is different, the client will upload the file, as
above.

If these identifiers are the same, the client will assume that the file is
unchanged (unless the ``--ignore-timestamps`` option is provided, in which
case the client always re-uploads the file), and it may be allowed to skip
the upload. For safety, however, we require the client periodically perform a
filecheck on these probably-already-uploaded files, and re-upload anything
that doesn't look healthy. The client looks the fileid up in the
'last_checked' table, to see how long it has been since the file was last
checked.

A "random early check" algorithm should be used, in which a check is
performed with a probability that increases with the age of the previous
results. E.g. files that were last checked within a month are not checked,
files that were checked 5 weeks ago are re-checked with 25% probability, 6
weeks with 50%, more than 8 weeks are always checked. This reduces the
"thundering herd" of filechecks-on-everything that would otherwise result
when a backup operation is run one month after the original backup. If a
filecheck reveals the file is not healthy, it is re-uploaded.

If the filecheck shows the file is healthy, or if the filecheck was skipped,
the client gets to skip the upload, and uses the previous filecap (from the
'caps' table) to add to the parent directory.

If a new file is uploaded, a new entry is put in the 'caps' and 'last_upload'
table, and an entry is made in the 'local_files' table to reflect the mapping
from local disk pathname to uploaded filecap. If an old file is re-uploaded,
the 'last_upload' entry is updated with the new timestamps. If an old file is
checked and found healthy, the 'last_upload' entry is updated.

Relying upon timestamps is a compromise between efficiency and safety: a file
which is modified without changing the timestamp or size will be treated as
unmodified, and the "``tahoe backup``" command will not copy the new contents
into the grid. The ``--no-timestamps`` option can be used to disable this
optimization, forcing every byte of the file to be hashed and encoded.

Directory Operations
====================

Once the contents of a directory are known (a filecap for each file, and a
dircap for each directory), the backup process must find or create a tahoe
directory node with the same contents. The contents are hashed, and the hash
is queried in the 'directories' table. If found, the last-checked timestamp
is used to perform the same random-early-check algorithm described for files
above, but no new upload is performed. Since "``tahoe backup``" creates immutable
directories, it is perfectly safe to re-use a directory from a previous
backup.

If not found, the web-API "mkdir-immutable" operation is used to create a new
directory, and an entry is stored in the table.

The comparison operation ignores timestamps and metadata, and pays attention
solely to the file names and contents.

By using a directory-contents hash, the "``tahoe backup``" command is able to
re-use directories from other places in the backed up data, or from old
backups. This means that renaming a directory and moving a subdirectory to a
new parent both count as "minor changes" and will result in minimal Tahoe
operations and subsequent network traffic (new directories will be created
for the modified directory and all of its ancestors). It also means that you
can perform a backup ("#1"), delete a file or directory, perform a backup
("#2"), restore it, and then the next backup ("#3") will re-use the
directories from backup #1.

The best case is a null backup, in which nothing has changed. This will
result in minimal network bandwidth: one directory read and two modifies. The
``Archives/`` directory must be read to locate the latest backup, and must be
modified to add a new snapshot, and the ``Latest/`` directory will be updated to
point to that same snapshot.

