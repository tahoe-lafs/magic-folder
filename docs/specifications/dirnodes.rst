﻿.. -*- coding: utf-8-with-signature -*-

==========================
Tahoe-LAFS Directory Nodes
==========================

As explained in the architecture docs, Tahoe-LAFS can be roughly viewed as
a collection of three layers. The lowest layer is the key-value store: it
provides operations that accept files and upload them to the grid, creating
a URI in the process which securely references the file's contents.
The middle layer is the file store, creating a structure of directories and
filenames resembling the traditional Unix or Windows filesystems. The top
layer is the application layer, which uses the lower layers to provide useful
services to users, like a backup application, or a way to share files with
friends.

This document examines the middle layer, the "file store".

1.  `Key-value Store Primitives`_
2.  `File Store Goals`_
3.  `Dirnode Goals`_
4.  `Dirnode secret values`_
5.  `Dirnode storage format`_
6.  `Dirnode sizes, mutable-file initial read sizes`_
7.  `Design Goals, redux`_

    1. `Confidentiality leaks in the storage servers`_
    2. `Integrity failures in the storage servers`_
    3. `Improving the efficiency of dirnodes`_
    4. `Dirnode expiration and leases`_

8.  `Starting Points: root dirnodes`_
9.  `Mounting and Sharing Directories`_
10. `Revocation`_

Key-value Store Primitives
==========================

In the lowest layer (key-value store), there are two operations that reference
immutable data (which we refer to as "CHK URIs" or "CHK read-capabilities" or
"CHK read-caps"). One puts data into the grid (but only if it doesn't exist
already), the other retrieves it::

 chk_uri = put(data)
 data = get(chk_uri)

We also have three operations which reference mutable data (which we refer to
as "mutable slots", or "mutable write-caps and read-caps", or sometimes "SSK
slots"). One creates a slot with some initial contents, a second replaces the
contents of a pre-existing slot, and the third retrieves the contents::

 mutable_uri = create(initial_data)
 replace(mutable_uri, new_data)
 data = get(mutable_uri)

File Store Goals
================

The main goal for the middle (file store) layer is to give users a way to
organize the data that they have uploaded into the grid. The traditional way
to do this in computer filesystems is to put this data into files, give those
files names, and collect these names into directories.

Each directory is a set of name-entry pairs, each of which maps a "child name"
to a directory entry pointing to an object of some kind. Those child objects
might be files, or they might be other directories. Each directory entry also
contains metadata.

The directory structure is therefore a directed graph of nodes, in which each
node might be a directory node or a file node. All file nodes are terminal
nodes.

Dirnode Goals
=============

What properties might be desirable for these directory nodes? In no
particular order:

1. functional. Code which does not work doesn't count.
2. easy to document, explain, and understand
3. confidential: it should not be possible for others to see the contents of
   a directory
4. integrity: it should not be possible for others to modify the contents
   of a directory
5. available: directories should survive host failure, just like files do
6. efficient: in storage, communication bandwidth, number of round-trips
7. easy to delegate individual directories in a flexible way
8. updateness: everybody looking at a directory should see the same contents
9. monotonicity: everybody looking at a directory should see the same
   sequence of updates

Some of these goals are mutually exclusive. For example, availability and
consistency are opposing, so it is not possible to achieve #5 and #8 at the
same time. Moreover, it takes a more complex architecture to get close to the
available-and-consistent ideal, so #2/#6 is in opposition to #5/#8.

Tahoe-LAFS v0.7.0 introduced distributed mutable files, which use public-key
cryptography for integrity, and erasure coding for availability. These
achieve roughly the same properties as immutable CHK files, but their
contents can be replaced without changing their identity. Dirnodes are then
just a special way of interpreting the contents of a specific mutable file.
Earlier releases used a "vdrive server": this server was abolished in the
v0.7.0 release.

For details of how mutable files work, please see :doc:`mutable`.

For releases since v0.7.0, we achieve most of our desired properties. The
integrity and availability of dirnodes is equivalent to that of regular
(immutable) files, with the exception that there are more simultaneous-update
failure modes for mutable slots. Delegation is quite strong: you can give
read-write or read-only access to any subtree, and the data format used for
dirnodes is such that read-only access is transitive: i.e. if you grant Bob
read-only access to a parent directory, then Bob will get read-only access
(and *not* read-write access) to its children.

Relative to the previous "vdrive server"-based scheme, the current
distributed dirnode approach gives better availability, but cannot guarantee
updateness quite as well, and requires far more network traffic for each
retrieval and update. Mutable files are somewhat less available than
immutable files, simply because of the increased number of combinations
(shares of an immutable file are either present or not, whereas there are
multiple versions of each mutable file, and you might have some shares of
version 1 and other shares of version 2). In extreme cases of simultaneous
update, mutable files might suffer from non-monotonicity.


Dirnode secret values
=====================

As mentioned before, dirnodes are simply a special way to interpret the
contents of a mutable file, so the secret keys and capability strings
described in :doc:`mutable` are all the same. Each dirnode contains an RSA
public/private keypair, and the holder of the "write capability" will be able
to retrieve the private key (as well as the AES encryption key used for the
data itself). The holder of the "read capability" will be able to obtain the
public key and the AES data key, but not the RSA private key needed to modify
the data.

The "write capability" for a dirnode grants read-write access to its
contents. This is expressed on concrete form as the "dirnode write cap": a
printable string which contains the necessary secrets to grant this access.
Likewise, the "read capability" grants read-only access to a dirnode, and can
be represented by a "dirnode read cap" string.

For example,
URI:DIR2:swdi8ge1s7qko45d3ckkyw1aac%3Aar8r5j99a4mezdojejmsfp4fj1zeky9gjigyrid4urxdimego68o
is a write-capability URI, while
URI:DIR2-RO:buxjqykt637u61nnmjg7s8zkny:ar8r5j99a4mezdojejmsfp4fj1zeky9gjigyrid4urxdimego68o
is a read-capability URI, both for the same dirnode.


Dirnode storage format
======================

Each dirnode is stored in a single mutable file, distributed in the Tahoe-LAFS
grid. The contents of this file are a serialized list of netstrings, one per
child. Each child is a list of four netstrings: (name, rocap, rwcap,
metadata). (Remember that the contents of the mutable file are encrypted by
the read-cap, so this section describes the plaintext contents of the mutable
file, *after* it has been decrypted by the read-cap.)

The name is simple a UTF-8 -encoded child name. The 'rocap' is a read-only
capability URI to that child, either an immutable (CHK) file, a mutable file,
or a directory. It is also possible to store 'unknown' URIs that are not
recognized by the current version of Tahoe-LAFS. The 'rwcap' is a read-write
capability URI for that child, encrypted with the dirnode's write-cap: this
enables the "transitive readonlyness" property, described further below. The
'metadata' is a JSON-encoded dictionary of type,value metadata pairs. Some
metadata keys are pre-defined, the rest are left up to the application.

Each rwcap is stored as IV + ciphertext + MAC. The IV is a 16-byte random
value. The ciphertext is obtained by using AES in CTR mode on the rwcap URI
string, using a key that is formed from a tagged hash of the IV and the
dirnode's writekey. The MAC is written only for compatibility with older
Tahoe-LAFS versions and is no longer verified.

If Bob has read-only access to the 'bar' directory, and he adds it as a child
to the 'foo' directory, then he will put the read-only cap for 'bar' in both
the rwcap and rocap slots (encrypting the rwcap contents as described above).
If he has full read-write access to 'bar', then he will put the read-write
cap in the 'rwcap' slot, and the read-only cap in the 'rocap' slot. Since
other users who have read-only access to 'foo' will be unable to decrypt its
rwcap slot, this limits those users to read-only access to 'bar' as well,
thus providing the transitive readonlyness that we desire.

Dirnode sizes, mutable-file initial read sizes
==============================================

How big are dirnodes? When reading dirnode data out of mutable files, how
large should our initial read be? If we guess exactly, we can read a dirnode
in a single round-trip, and update one in two RTT. If we guess too high,
we'll waste some amount of bandwidth. If we guess low, we need to make a
second pass to get the data (or the encrypted privkey, for writes), which
will cost us at least another RTT.

Assuming child names are between 10 and 99 characters long, how long are the
various pieces of a dirnode?

::

 netstring(name) ~= 4+len(name)
 chk-cap = 97 (for 4-char filesizes)
 dir-rw-cap = 88
 dir-ro-cap = 91
 netstring(cap) = 4+len(cap)
 encrypted(cap) = 16+cap+32
 JSON({}) = 2
 JSON({ctime=float,mtime=float,'tahoe':{linkcrtime=float,linkmotime=float}}): 137
 netstring(metadata) = 4+137 = 141

so a CHK entry is::

 5+ 4+len(name) + 4+97 + 5+16+97+32 + 4+137

And a 15-byte filename gives a 416-byte entry. When the entry points at a
subdirectory instead of a file, the entry is a little bit smaller. So an
empty directory uses 0 bytes, a directory with one child uses about 416
bytes, a directory with two children uses about 832, etc.

When the dirnode data is encoding using our default 3-of-10, that means we
get 139ish bytes of data in each share per child.

The pubkey, signature, and hashes form the first 935ish bytes of the
container, then comes our data, then about 1216 bytes of encprivkey. So if we
read the first::

 1kB: we get 65bytes of dirnode data : only empty directories
 2kB: 1065bytes: about 8
 3kB: 2065bytes: about 15 entries, or 6 entries plus the encprivkey
 4kB: 3065bytes: about 22 entries, or about 13 plus the encprivkey

So we've written the code to do an initial read of 4kB from each share when
we read the mutable file, which should give good performance (one RTT) for
small directories.


Design Goals, redux
===================

How well does this design meet the goals?

1. functional: YES: the code works and has extensive unit tests
2. documentable: YES: this document is the existence proof
3. confidential: YES: see below
4. integrity: MOSTLY: a coalition of storage servers can rollback individual
   mutable files, but not a single one. No server can
   substitute fake data as genuine.
5. availability: YES: as long as 'k' storage servers are present and have
   the same version of the mutable file, the dirnode will
   be available.
6. efficient: MOSTLY:
     network: single dirnode lookup is very efficient, since clients can
       fetch specific keys rather than being required to get or set
       the entire dirnode each time. Traversing many directories
       takes a lot of roundtrips, and these can't be collapsed with
       promise-pipelining because the intermediate values must only
       be visible to the client. Modifying many dirnodes at once
       (e.g. importing a large pre-existing directory tree) is pretty
       slow, since each graph edge must be created independently.
     storage: each child has a separate IV, which makes them larger than
       if all children were aggregated into a single encrypted string
7. delegation: VERY: each dirnode is a completely independent object,
   to which clients can be granted separate read-write or
   read-only access
8. updateness: VERY: with only a single point of access, and no caching,
   each client operation starts by fetching the current
   value, so there are no opportunities for staleness
9. monotonicity: VERY: the single point of access also protects against
   retrograde motion
     


Confidentiality leaks in the storage servers
--------------------------------------------

Dirnode (and the mutable files upon which they are based) are very private
against other clients: traffic between the client and the storage servers is
protected by the Foolscap SSL connection, so they can observe very little.
Storage index values are hashes of secrets and thus unguessable, and they are
not made public, so other clients cannot snoop through encrypted dirnodes
that they have not been told about.

Storage servers can observe access patterns and see ciphertext, but they
cannot see the plaintext (of child names, metadata, or URIs). If an attacker
operates a significant number of storage servers, they can infer the shape of
the directory structure by assuming that directories are usually accessed
from root to leaf in rapid succession. Since filenames are usually much
shorter than read-caps and write-caps, the attacker can use the length of the
ciphertext to guess the number of children of each node, and might be able to
guess the length of the child names (or at least their sum). From this, the
attacker may be able to build up a graph with the same shape as the plaintext
file store, but with unlabeled edges and unknown file contents.


Integrity failures in the storage servers
-----------------------------------------

The mutable file's integrity mechanism (RSA signature on the hash of the file
contents) prevents the storage server from modifying the dirnode's contents
without detection. Therefore the storage servers can make the dirnode
unavailable, but not corrupt it.

A sufficient number of colluding storage servers can perform a rollback
attack: replace all shares of the whole mutable file with an earlier version.
To prevent this, when retrieving the contents of a mutable file, the
client queries more servers than necessary and uses the highest available
version number. This insures that one or two misbehaving storage servers
cannot cause this rollback on their own.


Improving the efficiency of dirnodes
------------------------------------

The current mutable-file -based dirnode scheme suffers from certain
inefficiencies. A very large directory (with thousands or millions of
children) will take a significant time to extract any single entry, because
the whole file must be downloaded first, then parsed and searched to find the
desired child entry. Likewise, modifying a single child will require the
whole file to be re-uploaded.

The current design assumes (and in some cases, requires) that dirnodes remain
small. The mutable files on which dirnodes are based are currently using
"SDMF" ("Small Distributed Mutable File") design rules, which state that the
size of the data shall remain below one megabyte. More advanced forms of
mutable files (MDMF and LDMF) are in the design phase to allow efficient
manipulation of larger mutable files. This would reduce the work needed to
modify a single entry in a large directory.

Judicious caching may help improve the reading-large-directory case. Some
form of mutable index at the beginning of the dirnode might help as well. The
MDMF design rules allow for efficient random-access reads from the middle of
the file, which would give the index something useful to point at.

The current SDMF design generates a new RSA public/private keypair for each
directory. This takes considerable time and CPU effort, generally one or two
seconds per directory. We have designed (but not yet built) a DSA-based
mutable file scheme which will use shared parameters to reduce the
directory-creation effort to a bare minimum (picking a random number instead
of generating two random primes).

When a backup program is run for the first time, it needs to copy a large
amount of data from a pre-existing local filesystem into reliable storage.
This means that a large and complex directory structure needs to be
duplicated in the dirnode layer. With the one-object-per-dirnode approach
described here, this requires as many operations as there are edges in the
imported filesystem graph.

Another approach would be to aggregate multiple directories into a single
storage object. This object would contain a serialized graph rather than a
single name-to-child dictionary. Most directory operations would fetch the
whole block of data (and presumeably cache it for a while to avoid lots of
re-fetches), and modification operations would need to replace the whole
thing at once. This "realm" approach would have the added benefit of
combining more data into a single encrypted bundle (perhaps hiding the shape
of the graph from a determined attacker), and would reduce round-trips when
performing deep directory traversals (assuming the realm was already cached).
It would also prevent fine-grained rollback attacks from working: a coalition
of storage servers could change the entire realm to look like an earlier
state, but it could not independently roll back individual directories.

The drawbacks of this aggregation would be that small accesses (adding a
single child, looking up a single child) would require pulling or pushing a
lot of unrelated data, increasing network overhead (and necessitating
test-and-set semantics for the modification side, which increases the chances
that a user operation will fail, making it more challenging to provide
promises of atomicity to the user). 

It would also make it much more difficult to enable the delegation
("sharing") of specific directories. Since each aggregate "realm" provides
all-or-nothing access control, the act of delegating any directory from the
middle of the realm would require the realm first be split into the upper
piece that isn't being shared and the lower piece that is. This splitting
would have to be done in response to what is essentially a read operation,
which is not traditionally supposed to be a high-effort action. On the other
hand, it may be possible to aggregate the ciphertext, but use distinct
encryption keys for each component directory, to get the benefits of both
schemes at once.


Dirnode expiration and leases
-----------------------------

Dirnodes are created any time a client wishes to add a new directory. How
long do they live? What's to keep them from sticking around forever, taking
up space that nobody can reach any longer?

Mutable files are created with limited-time "leases", which keep the shares
alive until the last lease has expired or been cancelled. Clients which know
and care about specific dirnodes can ask to keep them alive for a while, by
renewing a lease on them (with a typical period of one month). Clients are
expected to assist in the deletion of dirnodes by canceling their leases as
soon as they are done with them. This means that when a client unlinks a
directory, it should also cancel its lease on that directory. When the lease
count on a given share goes to zero, the storage server can delete the
related storage. Multiple clients may all have leases on the same dirnode:
the server may delete the shares only after all of the leases have gone away.

We expect that clients will periodically create a "manifest": a list of
so-called "refresh capabilities" for all of the dirnodes and files that they
can reach. They will give this manifest to the "repairer", which is a service
that keeps files (and dirnodes) alive on behalf of clients who cannot take on
this responsibility for themselves. These refresh capabilities include the
storage index, but do *not* include the readkeys or writekeys, so the
repairer does not get to read the files or directories that it is helping to
keep alive.

After each change to the user's file store, the client creates a manifest and
looks for differences from their previous version. Anything which was removed
prompts the client to send out lease-cancellation messages, allowing the data
to be deleted.


Starting Points: root dirnodes
==============================

Any client can record the URI of a directory node in some external form (say,
in a local file) and use it as the starting point of later traversal. Each
Tahoe-LAFS user is expected to create a new (unattached) dirnode when they first
start using the grid, and record its URI for later use.

Mounting and Sharing Directories
================================

The biggest benefit of this dirnode approach is that sharing individual
directories is almost trivial. Alice creates a subdirectory that she wants
to use to share files with Bob. This subdirectory is attached to Alice's
file store at "alice:shared-with-bob". She asks her file store for the
read-only directory URI for that new directory, and emails it to Bob. When
Bob receives the URI, he attaches the given URI into one of his own
directories, perhaps at a place named "bob:shared-with-alice". Every time
Alice writes a file into this directory, Bob will be able to read it.
(It is also possible to share read-write URIs between users, but that makes
it difficult to follow the `Prime Coordination Directive`_ .) Neither
Alice nor Bob will get access to any files above the mounted directory:
there are no 'parent directory' pointers. If Alice creates a nested set of
directories, "alice:shared-with-bob/subdir2", and gives a read-only URI to
shared-with-bob to Bob, then Bob will be unable to write to either
shared-with-bob/ or subdir2/.

.. _`Prime Coordination Directive`: ../write_coordination.rst

A suitable UI needs to be created to allow users to easily perform this
sharing action: dragging a folder from their file store to an IM or email
user icon, for example. The UI will need to give the sending user an
opportunity to indicate whether they want to grant read-write or read-only
access to the recipient. The recipient then needs an interface to drag the
new folder into their file store and give it a home.

Revocation
==========

When Alice decides that she no longer wants Bob to be able to access the
shared directory, what should she do? Suppose she's shared this folder with
both Bob and Carol, and now she wants Carol to retain access to it but Bob to
be shut out. Ideally Carol should not have to do anything: her access should
continue unabated.

The current plan is to have her client create a deep copy of the folder in
question, delegate access to the new folder to the remaining members of the
group (Carol), asking the lucky survivors to replace their old reference with
the new one. Bob may still have access to the old folder, but he is now the
only one who cares: everyone else has moved on, and he will no longer be able
to see their new changes. In a strict sense, this is the strongest form of
revocation that can be accomplished: there is no point trying to force Bob to
forget about the files that he read a moment before being kicked out. In
addition it must be noted that anyone who can access the directory can proxy
for Bob, reading files to him and accepting changes whenever he wants.
Preventing delegation between communication parties is just as pointless as
asking Bob to forget previously accessed files. However, there may be value
to configuring the UI to ask Carol to not share files with Bob, or to
removing all files from Bob's view at the same time his access is revoked.

