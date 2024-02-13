.. -*- coding: utf-8 -*-

.. _conflicts:

Magic Folder Conflicts and Resolution
=====================================

When we have two or more participants updating a folder, it can happen that a file is modified "at the same time" by two or more participants.
More correctly, "at the same time" here means "between communications".

Effectively, participants are speaking a protocol by posting messages to their mutable Capabilities.
Namely, updading which Snapshot object a particular (file) name points to.

We do not know *when* another Participant has updated their notion of the current state.
However, what we *can* notice is when they "commit" to that state (that is, they update publically their current Snapshot pointer).

Each file is considered individually, and is represented by a tree of Snapshots of the contents (plus metadata).
Included in the metadata are one or more "parent" Snapshots (a brand new file has zero parents).

For more on this, the :ref:`datamodel` has a section on :ref:`datamodel_conflicts`.


Communication Between Folders
-----------------------------

Consider a single file named ``"foo"``.
When this file is created (for example on the device called Laptop) that device uploads a Snapshot (``S0``) with no parent Snapshots.

By "upload" we mean to push the content and metadata to the Tahoe-LAFS client, receiving ultimately a Capability for the Snapshot ``S0``.
The file entry for ``"foo"`` is then updated, visualized as: ``foo -> S0``

Now, all other Participants can (and, eventually, will) notice this update when they poll the magic-folder.

Each of these is a "communication event", so we talk about the regions between these as a cohesive state of that client.
That is, anything that happens during that time is "at the same time" for the purposes of this protocol.

Note that this time interval could be as short as a few seconds or as long as days (or more).

Observe too that the "parents" of a particular Snapshot are a commitment as to the state visible to that client when creating the Snapshot.


Detecting Conflicts
-------------------

A new update is communicated to us -- that is, we've downloaded a previously-unknown Snapshot from some other Participant's mutable Capbility.

We now look at our own Snapshot for the corresponding file.
Either our Snapshot appears in some parents (including grandparents, etc) of the new Snapshot, or it doesn't.

If it does appear, this is an "update" (and we simply replace the local content with the incoming content).

Instead if our Snapshot does not appear as any ancestor of the incoming Snapshot, a commit is determined.

This is because when the other device created their Snapshot, they didn't know about ours (or else it would appear as an ancestor) so we have made a change "at the same time".

Note that unlike tools like Git, we do not examine the contents of the file or try to produce differences -- everything is determined from the metadata in the Snapshot.


Showing Conflicts Locally
-------------------------

Once a conflict is detected, "conflict marker" files are put into the local magic folder location (our local file remains unmodified, and something like ``foo.conflict-desktop`` will appear.
The state database is also updated (conflicts can also be listed via the API and CLI)


Resolving Conflicts
-------------------

We cannot "magically" solve a conflict: two devices produced new Snapshots while not communicating with each other.

Thus, it is up to the humans using these devices to determine what happens.
Once appropriate changes are decided upon, a new Snapshot is produced with *two or more parents*: one parent for each of the Snapshots involved in the conflict (we've only talked about one other participant so far, but there could be more).

Such a Snapshot (with two or more parents) indicates to the other clients a particular resoltion to the conflict has been decided.

So there's another case when we see an incoming new Snapshot: it may in fact *resolve an existing* conflict.


Resolving via Filesystem
------------------------


Resolving via the CLI
---------------------


Resolving via the HTTP API
--------------------------

