.. -*- coding: utf-8 -*-

.. _snapshots:


Snapshots
=========

Information about files in a Magic Folder are encapsulated in a
logical "Snapshot" object. We distinguish between a "local" Snapshot
and a "remote" Snapshot (in code, these are `LocalSnapshot` and
`RemoteSnapshot`).

The difference is that a Remote Snapshot has been signed and uploaded
into the Tahoe-LAFS Grid. A Local Snapshot exists only on one device's
local disk (so other participants cannot see it yet).

A Snapshot has one or more parents, forming a Directed Asyclic Graph
(DAG). Snapshots are signed "as" they are uploaded into the Grid:
first the actual content is uploaded as an immutable, yielding a
read-only capability-string; then this string and other metadata is
signed. The signature, metadata and pointer to the content constitutes
the Remote Snapshot and itself is uploaded into the Tahoe Grid,
represented as an immutable directory.


Implementation Details
----------------------

Internally in the daemon we have a `LocalSnapshotService` instance
(which is a Twisted `IService` provider). This service consumes a
`DeferredQueue` of file-paths. These paths are given in turn to a
`LocalSnapshotCreator` object. This creates the actual `LocalSnapshot`
instances and serializes them. This causes the content to be copied to
a unique file in the "stash directory" and causes a new row in the
local-snapshots database table to be created.

After the database entry is created, we have completely captured the
Snapshot locally. A local Snapshot can have other local Snapshots as
parents (and of course Remote Snapshots can be parents too).

New file-paths appear in the queue according to other services in the
magic-folder daemon (XXX add details as they're implemented).

Once a new Local Snapshot is created it is added to the upload queue.


Uploading, Signing
------------------

We attempt uploads of Snapshots only after they are safely in our
local state. This allows failed uploads to re-start (even if our local
daemon crashes or is shut off during upload). It also allows for
proper offline operation where we can continue to create Snapshots
even if we can't communicate to Tahoe-LAFS.

The upload is a multi-part process:

- upload the content, yielding an immutable read-capability

- use the local keypair to sign the snapshot (requires the
  read-capability from the first step)

- create an immutable directory to represent the Snapshot (which
  encapsulates the metadata, content pointer and signature)

- modify our mutable directory to link to this latest Snapshot

After this, we call this Snapshot a "Remote Snapshot" because it is
represented in the Tahoe-LAFS Grid. Other users will discover it when
they next poll our mutable directory.

We cannot sign local Snapshots because we don't yet have the
capability-string for the content. The capability-string is only known
after we upload the content to Tahoe-LAFS.
