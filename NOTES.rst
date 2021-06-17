Persistent Formats
==================

remotesnapshotdb
................
- filename -> snapshot capability mapping
- contains our local view of our personal DMD
- we always write to this, and only then update the personal DMD
  - thus, this will always be equal to, or a descendant of the
    snapshot in the personal DMD
- calling this "remote_snapshots" is misleading, since we don't track snapshots
  from other particpants here
- given that we always want to pair updating the db with then updating our
  personal DMD, we should either:
  - have a function that does both
  - have a service that scans our db and updates the DMD
    (the second one could handle updating the db, and then queuing an DMD update)


Service Components
==================

RemoteSnapshotCacheService
..........................

