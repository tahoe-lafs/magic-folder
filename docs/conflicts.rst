.. -*- coding: utf-8 -*-

.. _conflicts:

Magic Folder Conflicts and Resolution
=====================================

When we have two or more participants updating a folder, it can happen that a file is modified "at the same time" by two or more participants.
More correctly, "at the same time" here means "between communications events".

Effectively, participants are speaking a protocol by posting updates to their mutable Capabilities.
Namely, updating which Snapshot object a particular (file) name points to (including adding a new name, or removing one).

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
Here, "updated" meanst that we use Tahoe-LAFS to edit to contents of our Personal directory to change "foo" to point at Snapshot ``S0``.

Now, all other Participants can (and, eventually, will) notice this update when they poll the magic-folder.

Each of these updates to our Personal directory is a "communication event", so we talk about the regions between these as a cohesive state of that client.
That is, anything that happens during that time is "at the same time" for the purposes of this protocol.

Note that this time interval could be as short as a few seconds or as long as days (or more).

Observe too that the "parents" of a particular Snapshot are a commitment to the state visible by a particular client when creating any new Snapshot.


Detecting Conflicts
-------------------

A new update is communicated to us -- that is, we've downloaded a previously-unknown Snapshot from some other Participant's mutable Capbility.

We now look at our own Snapshot for the corresponding file.
Either our Snapshot appears in some parents (including grandparents, etc) of the new Snapshot, or it doesn't.

If it does appear, this is an "update" (and we simply replace the local content with the incoming content).

Instead if our Snapshot does not appear as any ancestor of the incoming Snapshot, a conflict is determined.

This is because when the other device created their Snapshot, they didn't know about ours (or else it would appear as an ancestor) so we have made a change "at the same time".

Note that unlike tools like Git, we do not examine the contents of the file or try to produce differences -- everything is determined from the metadata in the Snapshot.
This means that even if we happened to make the very same edits "at the same time" it would still be a conflict.


Showing Conflicts Locally
-------------------------

Once a conflict is detected, "conflict marker" files are put into the local magic folder location (our local file remains unmodified, and something like ``foo.conflict-desktop`` will appear.
The state database is also updated (conflicts can also be listed via the API and CLI).

Although magic-folder itself doesn't try to examine the contents, you can now use any ``diff`` or similar tools you prefer to look at what is different between your copy and other participant(s) copies.


Resolving Conflicts
-------------------

We cannot "magically" solve a conflict: two devices produced new Snapshots while not communicating with each other.

Thus, it is up to the humans using these devices to determine what happens.
Once appropriate changes are decided upon, a new Snapshot is produced with *two or more parents*: one parent for each of the Snapshots involved in the conflict (we've only talked about one other participant so far, but there could be more).

Such a Snapshot (with two or more parents) indicates to the other clients a particular resoltion to the conflict has been decided.

So there's actually another case when we see an incoming new Snapshot: it may in fact *resolve an existing* conflict.
If this is the case, conflict markers are removed and the local database is updated (i.e. removing the conflict).

It is a human problem if this resolution is not to your particular liking; you can produce an edit again or talk to the human who runs the other computer(s) involved.
The history of these changes *is available* in the parent (or grandparent) Snapshots if the UX you're using can view or restore these.


Resolving via Filesystem
------------------------

Not currently possible.


Resolving via the CLI
---------------------

The subcommand ``magic-folder resolve`` may be used to specify a resolution.
It allows you to choose ``--mine`` or ``--theirs`` (if there is only one other conflict).
Otherwise, you must apply the ``--use <name>`` option to specify which version to keep.

Currently there is no API for doing something more complex (e.g. simultaneuously replacing the latest version with new content).

Complete example:

.. code-block:: console

    magic-folder resolve --mine ~/Documents/Magic/foo


Resolving via the HTTP API
--------------------------

See :ref:`api_resolve_conflict`


Future Directions
-----------------

We do not consider the current conflict functionality "done".
There are other features required to make this more robust and have a nicer user experience.

*Viewing old data*: While it is currently possible in the datamodel to view past versions of the files, we do not know of any UI that does this (and the CLI currently cannot).

*Restore old version*: Similarly, it is possible to produce a new Snapshot that effectively restores an older version of the same file.
We do not know of any UI that can do this.

*Completely new content*: As hinted above, it might be nice to be able to produce a resolution that is some combination of multiple versions (like one sometimes does with Git conflicts, for example).
While this isn't directly possible currently, you can always take the "closest" one via the existin conflict-resolution API and then immediately produce an edit that has the desired new content.

*Resolution via file manipulation*: Currently, filesystem manipulation is one API (e.g. you just change a file and new Snapshots are produced).
Similarly, conflict-marker files are used to indicate a conflict via the filesystem.
It would be nice if you could use a similar mechanism to *eliminate* conflicts -- one way to design this could be to notice that the user has deleted all the conflict-markers and take this as a sign that the remaining file is in fact the desired resolution.
