Magic-Folder discovers new files
================================


Contacts: meejah
Date:2021-06-03


When starting up the software, magic-folders should find new or changed
files since the last time it ran in every Magic Folder it is tracking.

Rationale
---------

This is a core function of magic-folders.

User stories
------------


Start magic-folders a Second Time
`````````````````````````````````

As a user, I want local filesystem changes to be discovered and uploaded
so that the Snapshots in Tahoe represent my local filesystem state

Discover the State of Synchronization
`````````````````````````````````````

As a user, I wish to know whether magic-folders has currently queued
more work to do and how long ago it was since it checked for more work
to do.


Constraints and requirements
----------------------------

Must
````

* New files are discovered (a “new file” is one that wasn’t present
  when magic-folders was last turned off). Note that this includes
  files in sub-directories (recursively) below our Magic Folder.

* Local files that are different from the Tahoe representation are
  discovered

* Deleted files are discovered (a “deleted file” is one that was
  present when magic-folders was last turned off but is no longer
  present).

Nice to have
````````````

Must not
````````

* Files that are not changed should not be re-uploaded (a “not
  changed” file is one that was present when magic-folders was last
  turned off and is still present with the same bytes as content)

Out of scope
------------

Things that are definitely not included that people ask about.

Success
-------

The state of our Personal DMD in Tahoe should match the state of our
local filesystem at some point after we re-start the magic-folders
software. “Match the state” here means:

* Any file present below our Magic Folder has a Snapshot present in
  Tahoe

* The “content” subdir of that Snapshot points at an immutable
  capability that matches the bytes we have on disk

* Any file that used to be present but no longer exists locally
  should have an empty / missing “content” subdir in its Snapshot on
  Tahoe (that is, there will still be an entry in Tahoe but it will
  indicate that the corresponding local file is deleted).

There is some vagueness above about knowing when the synchronization is
completed. It should be possible to use the status API to discover if
magic-folders has work queued. The above checks should be valid after:

* Magic-folders has started up successfully

* The status-API indicates there is no (upload) work in progress
   (``synchronizing=false``)

* The status-API indicates a “last-scan” time completed after the
  time when magic-folders was re-started.

How will we measure how well we have done?
------------------------------------------


Alternatives considered
-----------------------

* Watchman (https://facebook.github.io/watchman/). A client/server
  c++ daemon. Monitors the filesystem (using native APIs like inotify)
  and supports queries etc via a client CLI or API.


Detailed design
---------------

Version control systems usual use some values from ``stat(2)`` to detect when a
file has changed on-disk. For example, mercurial uses size and mtime, and old
magic-folder uses size, mtime and ctime.

For the scanner (see below) to be able to check against those values, we need
to store those values when we notice an update to a file. There are currently
two places we need to do that:

* When we take a local snapshot, we need to record that information.
* When we overwrite a file with a remote snapshot, we need to record the
  information of the file that got written. In this case, we want to record the
  information from the staged file, before we move it into place, to ensure
  that writes that happen after we move the file into place are detected.
* We *dont'* need to capture the information when we turn a local snapshot into
  a remote snapshot, since we will have already recorded that information.

Scanner
.......

When triggered, the scanning system walks the entire tree of files below
the Magic Folder.

As we use ``twisted.python.filepath.FilePath`` for many other
operations within magic-folder we will use ``FilePath.walk()`` to
traverse the files.  This produces a generator; we should take care to
yield control back to the reactor occasionally so that we don’t starve
network reads/writes if we are descending a lot of files.

For each file found:

* Mangle / flatten the name to a Snapshot name

* Check if we already have a Snapshot for this name:

   * If we have a snapshot, we'll have recorded (mtime, ctime, size) of the
     file when it was snapshotted. 

      * If the values match, we don't create a snapshot.
      * If the values don't match, we request a snapshot be created of the file.

   * ..or a RemoteSnashot

      * Compare the file contents to the Snapshot:

         * include st_mtime in the remotesnapshot table (or a new
           table) representing the modification time of the local file
           when we last uploaded (or downloaded) this Snapshot.

      * If the st_mtime is different, produce a new LocalSnapshot as
        a child of the RemoteSnapshot

   * ..or no snapshots at all yet

      * Create a new LocalSnapshot with no parent

Data integrity
--------------

Do not do overlapping scans. There should be either zero or one scans
happening at any given time for any given Magic Folder.

Consider the uploader: it may be turning LocalSnapshots into
RemoteSnapshots at the moment.

* If LocalSnapshots for a given file are currently being uploaded
  there is a window between when they were deserialized but before the
  local data is deleted when a newly added LocalSnapshot will be
  dropped (see the ticket for this though:
  https://github.com/LeastAuthority/magic-folder/issues/385 ). Fixing
  that ticket should handle this problem.

Consider the downloader: it may be discovering new RemoteSnapshots and
downloading them.

* Is there any point where the scanner may be confused by a
  newly-appearing file from the downloader and falsely believe it
  should upload it?

   * The file is first downloaded to a stash area and then moved into
     place. So I think the scanner will only see the updated file
     (along with the corresponding st_mtime in the database) or not.
     Both cases should result in no change detected.

Consider the user: they may be changing or adding files at any moment
(e.g. during the scan).

* I think the only possible consequence here is that a file is
  discovered later than it might otherwise be. E.g. the user makes a
  new file appear in a directory we already walked

* The old magic-folder did take some pains to consider the case where
  a user is actively editing a file or otherwise making rapid changes.
  However, this was using inotify too so changes were discovered more
  rapidly (e.g. on file creation and save). I don’t see a compelling
  case to duplicate any such logic here.


Security
--------

We must not “discover” a file outside the magic-folder directory.

Backwards compatibility
-----------------------

None.

Performance and scalability
---------------------------

When scanning a large directory we must take care not to pause the
reactor for “too long”


Further reading
---------------

