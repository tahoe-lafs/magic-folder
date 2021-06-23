Questions
=========

short-term
----------

- Do we want to keep multiple local snapshots and publish all of them, or do we
  want to skip ones intermediate ones that we have not yet uploaded.

  - keeping them is a feature (for "version control")
  - keeping them gives us better ability to preserve history,
  - skipping them means less storage used, which may reduce the impact of 
    https://whetstone.privatestorage.io/privatestorage/bizops/-/issues/170#note_13921

- Do we need to ensure that we only ever have a single instance of the service
  running at the same time?

  - yes.
  - How do we detect if we are running?

long-term
---------

- (resolved?) Will the snapshot in the remotesnapshotdb ever not be a descendant of the one in the Personal DMD?

  - We might choose to do this if two machines created a file, and we choose to resolve
    entirely in favor of the other remote?
  - A third machine may have observed *our* snapshot, and have a descendant of it. Breaking
    the link would then force that conflict to be resolved again, when syncing with that
    machine.

- is there a reason that snapshots store their name? this is related to noticing file moves


- writing files atomically:
  windows:

  - https://antonymale.co.uk/windows-atomic-file-writes.html
    - https://web.archive.org/web/20160115141959/http://blogs.msdn.com/b/adioltean/archive/2005/12/28/507866.aspx
  - https://pypi.org/project/atomicwrites/
  - https://docs.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilea
    - via https://docs.microsoft.com/en-us/windows/win32/fileio/deprecation-of-txf

  unix:

  - renameat2 with RENAME_EXCHANGE

random
------
- attrs validators get passed self, so we could use one to call setServiceParent for services
  This could be used for e.g. magic_folder.magic_folder.MagicFolder

Persistent Formats
==================

remotesnapshotdb
----------------
- filename -> snapshot capability mapping
- contains our local view of our personal DMD

- we always write to this, and only then update the personal DMD

  - thus, this will always be equal to, or a descendant of the
    snapshot in the personal DMD

- calling this "remote_snapshots" is misleading, since we don't track snapshots
  from other particpants here (this causes confusion, because snapshots here are
  explicitly not RemoteSnapshotCacheService)
- given that we always want to pair updating the db with then updating our
  personal DMD, we should either:

  - have a function that does both
  - have a service that scans our db and updates the DMD
    (the second one could handle updating the db, and then queuing an DMD update)

Personal DMD
------------

- filename -> snapshot capability mapping
- This is the durable record of the state of files on the local machine; that is,
  if this machine were to be lost, only things reflected in the personal DMD would
  be recoverable.
- the snapshot listed here will always be a ancestor of the local snapshot
- only a single machine should ever be writting to a single DMD, so in theory, we
  should alway know what is in the the personal DMD (modulo crashes/failures)
  
  - should we enforce this by only providing read-only caps for use in recovery info
  - do we want to try and notice if it gets changed out from under us?

    -> no.
    - we could do this by keeping track of the cap we are about to write, and
      the cap we have written last. The invariant would be that the cap in the
      DMD will always be one of those two. We can recover by writing either the
      cap we were about to write, or a newer one(after having updae the one we
      are about to write).
    - Looking at tahoe code, there is not any kind of atomic compare-and-set we
      can perform on tahoe directories, so there will always be a race condition
      we can't detect where someone else has written to the DMD as we are about
      to write to it.

    -> no, you can't have two clients writing to the same DMD. That's
       why two instances can't ever have the same writecap.


Service Components
==================

RemoteSnapshotCacheService
--------------------------
- capability ->  ``RemoteSnapshot``  mapping cache
- not currently persisted locally
- downloads until we reach the *current* entry in the remotesnapshotdb of the name

  -> this optimization was removed

  - note that as time passes, the snapshot we stop at will get newer, so we
    will not have all the parents of earlier cached snapshots, as we would have
    if we had cached them later

- FIXME: ``MagicFolderUpdaterService``'s docstring says it depends on
  ``add_remote_capability`` caching all\* parents of the capability will
  by the time it returns but that will often not be the case

  - also "add" doesn't seem like a good name, "get" is much closer to what it is doing

- why does this use a queue?

 - tahoe can likely respond to several requests at once
 - it makes control flow harder to follow
 - I suspect that it can cause surpising delays when one caller is has requested
   a snapshot with a long chain of parents, but won't care about them, and another
   caller is blocked from getting a response until all the parents of the first are
   downloaded
 - while it may make sense to start downloading parents automatically, I think it
   would make sense to prioritize (or respond directly to; this would require some
   care to avoid requesting the same snapshot more than once) direct requests

- we should probably encapsulate ``.cached_snapshots`` behind a function; I'm not
  sure why outside consumers can't just use this to trigger a download if it is not cached

DownloaderService
=================

- Service that periodically polls other personal DMDs for new snapshots
- for each participant
  - for each file
    - if the snapshot in that DMD doesn't match the snapshot in our remotesnapshotdb
      - enqueue that snapshot for processing
- Improvements
  - scan all DMD's for each file, and enqueue them all for processing together
  - I was going to suggest that we shouldn't queue up multiple snapshots for
    the same name (particularly from the same remote) which is true. But since
    as long this waits for :py:`MagicFolderUpdaterService.add_remote_snapshot`
    to return, we will always wait for the queue to empty before queuing up a new
    snapshot.

MagicFolderUpdaterService
=========================
Update a given file based on a provided snapshot.

- dowloads data to temporary file  (FIXME: this should only happen if we will use it)
- checks if the given snapshot is a conflict:

  - if there is a local file

    - if we have a localsnapshot -> conflict
    - if we have a remotesnap

      - if our remote snapshot is an ancestor of the given snapshot -> not conflict
      - otherwise -> conflict

  - if there is not local file

    - if we have a local or remote snapshot

      - currently an error, as we don't handle deletes

    - otherwise -> not conflict

- move download data into position:

  - if conflict -> write file as "<filename>.conflict-<snapshot author>"
  - if not conflict -> write at name

- write provided snapshot as our remotesnapshot

  - what if there was a conflict, or our snapshot is newer than the provided one

- write the snapshot from our remotesnapshotdb to our personal DMD


Things that are missing:
- checking if the provided snapshot is an ancestor of our snapshot

  - currently we will detect this case as a conflict

- checking if there are local changes since we last scanned, before overwriting
  the local file, if the provide snapshot is newer
