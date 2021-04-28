
.. -*- coding: utf-8 -*-

.. _downloader:

Downloader Operation
====================

Audience: developers working on magic-folder itself

This describes the general operation of the remote to local
synchronization as outlined in "the Leif Design" (see :ref:`Leif's
Proposal: Magic-Folder "single-file" snapshot design`).

We do not describe the synchronization of the list of participants in
a magic-folder. This list will consist of at least:

- an arbitrary ``name`` for each participant
- a directory read-capability for each participant ("personal DMD")
  - in this directory is a flattened representation of every file in
    the Magic Folder (similar to the Tahoe 1.14.0 design) with each
    entry pointing to a Snapshot. That is, there are no
    sub-directories to recurse into, all files are listed by
    downloading just the "personal DMD" capability of that participant.

"A Snapshot" is a single version of a single file. It is represented
by an immutable directory and contains:

- ``content``: (optional) a read-only Capability to the actual content of
  this Snapshot. Deletion Snapshots contain no such key.
- ``metadata``: information about the Snapshot, a read-only Capability pointing
  to a JSON-serialized dict containing:
  - ``snapshot_version``: int(1) currently
  - ``name``: the name of this snapshot (a mangled relative path)
  - ``author``: a dict containing:
    - ``name``: arbitrary name
    - ``verify_key``: base64-encoded public key of the author
  - ``parents``: a list of immutable Capability-strings of any parent Snapshots
  - additionally, in the Tahoe metadata for this metadata-capability
    is a ``magic_folder`` dict with the following keys:
    - ``author_signature``: base64-encoded signature which signs the
      content-Capability, metadata-Capability and name

If there are zero parents, this is the first version of a
file. Otherwise, it is a modification. If there are two or more
parents this version is the resolution of a conflict.

When reading the code, there is a method
``create_snapshot_from_capability`` which downloads a
capability-string from Tahoe and returns a ``RemoteSnapshot`` instance
(after verifying signatures).


General Operation
-----------------

There is a service responsible for deciding which Snapshots to
download. The capability strings of Snapshots to download are given to
a second service that is responsible for actually downloading
them. Downloading new Snapshots from other participants ultimately
causes changes to the local filesystem (in the magic-folder).


What Snapshots to Download
--------------------------

Each configured magic-folder has a ``collective_dircap`` which is a
Tahoe-LAFS Directory Capability ("dircap") for the list of
participants. If this dircap is writable then this device is the
administrator (and the only device which can modify the participants).

In either case, the capability can be downloaded. It will be a Tahoe-LAFS
directory containing a series of sub-directories; these are the
participants. The directory name is their name and points at a
Read Capability where all the files in their magic-folder are
stored. This is known as the "Personal DMD", where DMD stands for
"Distributed Mutable Directory".

The entries in a user's Personal DMD are flat (no subdirectories) and
point a (mangled) relative path-name to a Snapshot.

So, Snapshots to download can be discovered by:

- reading the Collective DMD
- for each user in it:
  - read their Personal DMD
  - for each entry in that directory:
    - queue the Snapshot for download (unless already cached)
- any newly-downloaded Snapshot should be examined; if any of its
  parents are not cached, download them too


Downloading Snapshots
---------------------

A service awaits capability-strings of Snapshots to download. For each
one, the function ``create_snapshot_from_capability`` is used to
download the capability and return a ``RemoteSnapshot`` instance.

This ``RemoteSnapshot`` is serialized to a local cache in the
magic-folder's state database.

We also arrange to make local filesystem changes (how?). This might
require waiting to download more ``RemoteSnapshots`` if it has any
parents that aren't cached.

Conflict Resolution is described in :ref:`Multi-party Conflict
Detection` under the Leif's Design section. Briefly: a
``RemoteSnapshot`` is traced through its parents until a common
ancestor is found. If the new Snapshot is a descendant of our latest
Snapshot for that name, it's an overwrite. If it is not, there is a
conflict (unless we don't yet have that name at all, then it's a
creation).


On Overwrite
------------

The ``content`` of the ``RemoteSnapshot`` is downloaded and moved into
place in our Magic Folder. Our Personal DMD is updated to point at
this Snapshot.

In case there is no ``content`` this is a delete and we simply remove
the corresponding local file. (Do we update our Personal DMD to point
at nothing?)

Note that a completely new file (a "create") is the same as a normal
overwrite (except of course there's no possibility of a conflict).


On Conflict
-----------

The ``content`` of the ``RemoteSnapshot`` is downloaded and moved into
a "conflict file" (see Leif Design) beside the conflicting
content. The Personal DMD is **not** updated. Once the conflict is
"resolved" then a new Snapshot is created with two parents: the latest
Snapshot we had at conflict time and the conflicting Snapshot. Our
Personal DMD is updated to point at this new Snapshot.

"Resolving" a snapshot is currently noticed via more filesystem
manipulation: the ``.confict`` file is deleted or moved (and the
existing file is taken to be the new content). For example, deciding
"I like the other device's file better" would mean moving the
``.conflict`` file over top of the existing one. Deciding "I like mine
better" means simply deleting the ``.conflict`` file. A more-complex
strategy of merging the contents would mean updating the existing file
**before** deleting the ``.conflict`` file.

I believe the above accurately describes what Tahoe 1.14.0
magic-folder does.

This doesn't mean it's the best "API" for conflict resolution (nor
does it need to remain the only one). In fact, it likely is not a good
API for any but motivated, advanced users and also seems like a bad
API for other programs.

In keeping with other new development in magic-folder, there is an
explicit HTTP API to resolve a conflict. For now, we limit this to
selecting "mine" or "theirs". A future extension might wish to provide
a way to provide completely new content (e.g. if the user edited a
diff, for example).


``GET /v1/conflicts/<folder-name>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a list (possibly empty) of strings for each path that is currently in a Conflict state in the given magic-folder.

The each string is the path relative to the selected magic-folder.

Our content is in the path itself. The conflicting "other" content is in ``<path>.theirs.<name>`` where ``<name>`` is the petname of the user who is provided the conflicted content.

Justification: we need somewhere for "theirs" versis "my" content .. I think we should still reflect this on the filesystem, even if the *API to manipulate it* is no longer there. This makes it more obvious for CLI users that they should check the conflicts list; the only alternative would seem to be "run some command occasionally to check for conflicts". I will forget to do this.


``POST /v1/resolve_conflict/<folder-name>?path=<some-path>&resolution=<theirs|mine>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``path`` query argument is required.
It must be a filesystem path relative to the selected magic-folder.

The ``resolution`` query argument is required.
It must be either the string ``theirs`` or the string ``mine``.

It is an error if the given ``path`` in the given magic-folder is not
currently in a conflicted state.

If the resolution is ``theirs`` then the file at ``<path>.theirs.<name>`` is moved to ``<path>`` and a new (local) Snapshot is created (with two parents).

If instead the resolution is ``mine`` then the file at ``<path>.theirs.<name>`` is deleted and a new (local) Snapshot is created (with two parents).

The response is delayed until the local state tracking the new Snapshot has been created.
