
.. -*- coding: utf-8 -*-

.. _downloader:

Downloader Operation
====================

Audience: developers working on magic-folder itself

**Note:** some of this document describes features that aren't yet implemented; those are noted with (Not implemented).

This describes the general operation of the remote to local synchronization.
The overall design was first articulated in "the Leif Design" (see :ref:`Leif's Proposal: Magic-Folder "single-file" snapshot design`).

We do not describe the synchronization of the list of participants in a magic-folder.
This list will consist of at least:

- an arbitrary ``name`` for each participant
- a directory read-capability for each participant ("Personal DMD")
- in this directory is a flattened representation of every file in  the Magic Folder (similar to the Tahoe 1.14.0 design) with each entry pointing to a Snapshot.
  That is, there are no sub-directories to recurse into: all files are listed by downloading just the "personal DMD" capability of that participant.

"A Snapshot" is a single version of a single file.
It is represented by an immutable directory and contains:

- ``content``: (optional) a read-only Capability to the actual content of this Snapshot.
  Deletion Snapshots contain no such key.
- ``metadata``: information about the Snapshot, a read-only Capability pointing to a JSON-serialized dict containing:
  - ``snapshot_version``: int(1) currently
  - ``name``: the name of this snapshot (a relative path)
  - ``author``: a dict containing:
    - ``name``: arbitrary name
    - ``verify_key``: base64-encoded public key of the author
  - ``parents``: a list of immutable Capability-strings of any parent Snapshots
  - (not yet) additionally, in the Tahoe metadata for this metadata-capability is a ``magic_folder`` dict with the following keys:
    - ``author_signature``: base64-encoded signature which signs the content-Capability, metadata-Capability and name

If there are zero parents this is the first version of a file.
If there is one parent it is a modification.
If there are two or more parents this version is the resolution of a conflict.

When reading the code, there is a method ``create_snapshot_from_capability`` which downloads a capability-string from Tahoe and returns a ``RemoteSnapshot`` instance.


General Operation
-----------------

The ``DownloaderService`` is responsible for deciding which Snapshots to download.
The capability strings of Snapshots to download are given to ``RemoteSnapshotCacheService`` which downloads a Snapshot and all its relevant parents into memory.
Once a Snapshot is downloaded it is passed to the ``MagicFolderUpdaterService`` which stages the actual content and determins if the change is an "overwrite" or a "conflict" and changes the local filesystem accordingly.

Downloading new Snapshots from other participants ultimately causes changes to the local filesystem (in the magic-folder).

(Not implemented). It may be the case that one or more parent Snapshots are missing.
In this case, we cannot cache or otherwise see those Snapshots and must take local action accordingly; see the section on conflicts for more.


What Snapshots to Download
--------------------------

Each configured magic-folder has a ``collective_dircap`` which is a Tahoe-LAFS Directory Capability ("dircap") for the list of participants.
If this dircap is writable then this device is the administrator (and the only device which can modify the participants).

In either case, the capability can be downloaded.
It will be a Tahoe-LAFS directory containing a series of sub-directories; these are the participants.
The directory name is the Participant name and points at a directory read-capability where all the files in their magic-folder are stored.
This directory is known as the "Personal DMD" (where DMD stands for "Distributed Mutable Directory").

The entries in a user's Personal DMD are flat (no subdirectories) and point a (mangled) relative path-name to a Snapshot.

So, Snapshots to download are discovered by:

- reading the Collective DMD
- for each user in it:
  - read their Personal DMD
  - for each entry in the Personal DMD:
    - queue the Snapshot for caching, including all parents (see note)
    - after download, queue the Snapshot for updates

Note that we don't always download *every* parent; we stop early if a common ancestor is found (see below).


Downloading Snapshots
---------------------

The ``RemoteSnapshotCacheService`` awaits capability-strings of Snapshots to download.
For each one, the function ``create_snapshot_from_capability`` is used to download the capability and return a ``RemoteSnapshot`` instance.
This ``RemoteSnapshot`` is stored in memory.

Additionally, all parents of the Snapshot are also cached.
We can stop caching parents once we find a "common ancestor"; this means a parent in the remote snapshot that matches the one in our Personal DMD.

.. note:

    This is really checking if our current snapshot is an ancestor of the given snapshot;
    if there is a conflict between this and another particpant, then the common ancestor
    is not our current snapshot

Conflict Resolution is described in :ref:`Multi-party Conflict Detection` under the Leif's Design section.
Briefly: a ``RemoteSnapshot`` is traced through its parents until a common ancestor is found.
If the new Snapshot is a descendant of our latest Snapshot for that name, it's an overwrite.
If it is not, there is a conflict (unless we don't yet have that name at all, then it's a creation).
(Not implemented). If we cannot traverse all parents due to missing Snapshots and have still failed to find a common ancestor we must assume it is a conflict.
(Not implemented). If there is a local file "in the way" for which no ``LocalSnapshot`` already exists it is also a conflict.


On Overwrite
------------

The ``content`` of the ``RemoteSnapshot`` is downloaded and moved into place in our Magic Folder.
The local ``remotesnapshot`` database table is updated to point at our new ``RemoteSnapshot``.
Our Personal DMD is updated to point at this Snapshot.

In case there is no ``content`` this is a delete and we simply remove the corresponding local file.

Note that a completely new file (a "create") is the same as a normal overwrite (except of course there's no possibility of a conflict).


On Conflict
-----------

The ``content`` of the ``RemoteSnapshot`` is downloaded and moved into a "conflict file" (see Leif Design) beside the conflicting content.
The Personal DMD is **not** updated.
(Not implemented). Once the conflict is "resolved" then a new Snapshot is created with two parents: the latest Snapshot we had at conflict time and the conflicting Snapshot.
Our Personal DMD is updated to point at this new Snapshot.

(Not implemented). "Resolving" a snapshot will be noticed via more filesystem manipulation: the ``.confict`` file is deleted or moved (and the existing file is taken to be the new content).
For example, deciding "I like the other device's file better" would mean moving the ``.conflict`` file over top of the existing one.
Deciding "I like mine better" means simply deleting the ``.conflict`` file.
A more-complex strategy of merging the contents would mean updating the existing file **before** deleting the ``.conflict`` file.

(Meejah believes the above accurately describes what Tahoe 1.14.0 magic-folder does).

This doesn't mean it's the best "API" for conflict resolution (nor does it need to remain the only one).
In fact, it likely is not a good API for any but motivated, advanced users and also seems like a bad API for other programs.

(Not implemented). In keeping with other new development in magic-folder, there is an explicit HTTP API to resolve a conflict.
For now, we limit this to selecting "mine" or "theirs".
A future extension might wish to provide a way to provide completely new content (e.g. if the user edited a diff, for example).
