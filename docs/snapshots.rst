.. -*- coding: utf-8 -*-

.. _snapshots:

Audience
========

This document is aimed at programmers and advanced users who want to
explore the inner working of the magic-folder feature that preserves
file history.

Motivation
==========

It would be beneficial to maintain different versions of a file that
uses are sharing with other parties. Another benefit is for better
detection of conflicts. Even though not a direct motivation, it would
be nice to get rid of the "root" privileges of the user who creates
the folder and invites other users.

To address these concerns, Leif Ryge proposed an alternative design on
which we base our design of snapshots.

Snapshots
=========

Information about files in a Magic Folder are encapsulated in a
logical "Snapshot" object. We distinguish between a "local" Snapshot
and a "remote" Snapshot (in code, these are `LocalSnapshot` and
`RemoteSnapshot`).

The difference is that a Remote Snapshot has been signed and uploaded
into the Tahoe-LAFS Grid. A Local Snapshot exists only on one device's
local disk (so other participants cannot see it yet).

A Snapshot has zero or more parents, forming a Directed Acyclic Graph
(DAG). Snapshots are signed "as" they are uploaded into the Grid:
first the actual content is uploaded as an immutable, yielding a
read-only capability-string; then this string and other metadata is
signed. The signature, metadata and pointer to the content constitutes
the Remote Snapshot and itself is uploaded into the Tahoe Grid,
represented as an immutable directory.


Content and Metadata
--------------------

Snapshots consist of two main pieces: the content and the
metadata. Both of these are immutable documents in Tahoe-LAFS. The
content is straightforward: it is an immutable-capability of the bytes
that the user had on disk when the Snapshot was created.

The "metadata" is a valid JSON document which is a dict. It is also
represented as an immutable capability. The metadata dict must contain
the following information:

- snapshot_version: an integer, 1 or bigger
- name: same as LocalSnapshot.name
- author: a dict containing:
  - name: arbitrary name of the author
  - verify_key: base64-encoded public-key of the author
- parents: a list containing immutable directory capability-strings, one for each parent (will be empty if there are no parents)


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

- upload the content yielding an immutable read-capability

- upload the metadata yielding an immutable read-capability

- use the local keypair to sign the snapshot (requires the
  read-capabilities from the first two steps)

- create an immutable directory to represent the Snapshot (which
  encapsulates the metadata, content pointer and signature)

- modify our Personal DMD to link to this latest Snapshot

- delete the local snapshot from our database

After this, we call this Snapshot a "Remote Snapshot" because it is
represented in the Tahoe-LAFS Grid. Other users will discover it when
they next poll our Personal DMD.

We cannot sign local Snapshots because we don't yet have the
capability-string for the content. The capability-string is only known
after we upload the content to Tahoe-LAFS.


Signature Details
-----------------

As above, we upload the content and metadata as two distinct immutable
capabilities. There is also the "name" of the file. A mangled version
of this name is also used to point at the Snapshot from the
Personal DMD.

The signature scheme is to concatenate a fixed string
(``magic-folder-snapshot-v1``), then the content capability, then the
metadata capability, then the name -- all with trailing newlines. This
is encoded to bytes with UTF8 and the author's signing key is used to
produce a signature. The signing key is a `nacl.signing.SigningKey`
from the PyNaCl library (using ``libsodium`` under the hood). See
https://pynacl.readthedocs.io/en/latest/signing/

For example, a particular Snapshot might be represented like this for
signing (the newlines are shown escaped for clarity)::

    magic-folder-snapshot-v1\n
    URI:CHK2:aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb:1:1:256\n
    URI:CHK2:yyyyyyyyyyyyyyyy:zzzzzzzzzzzzzzzz:1:1:256\n
    arbitrary_name\n

The resulting signature is base64-encoded and included in the "tahoe
metadata" for the "metadata capability" entry in the Snapshot's
immutable directory.

To verify the signature, a client has the content and metadata
capability-strings when they download the Snapshot object and the
mangled name from that participant's Personal DMD. They can thus
re-form the above text and verify the signature using the
participant's public-key before downloading the content or metadata.

**Note**: "metadata" is used in two ways above; Tahoe allows you to
add arbitrary metadata for each entry in a directory. This is where
we locate the base64-encoded signature itself. The "metadata about
the Snapshot" is its own capability (consisting of JSON-encoded
metadata).
