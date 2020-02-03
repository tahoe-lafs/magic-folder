﻿.. -*- coding: utf-8-with-signature -*-

=============
Mutable Files
=============

1.  `Mutable Formats`_
2.  `Consistency vs. Availability`_
3.  `The Prime Coordination Directive: "Don't Do That"`_
4.  `Small Distributed Mutable Files`_

    1. `SDMF slots overview`_
    2. `Server Storage Protocol`_
    3. `Code Details`_
    4. `SMDF Slot Format`_
    5. `Recovery`_

5.  `Medium Distributed Mutable Files`_
6.  `Large Distributed Mutable Files`_
7.  `TODO`_

Mutable files are places with a stable identifier that can hold data that
changes over time. In contrast to immutable slots, for which the
identifier/capability is derived from the contents themselves, the mutable
file identifier remains fixed for the life of the slot, regardless of what
data is placed inside it.

Each mutable file is referenced by two different caps. The "read-write" cap
grants read-write access to its holder, allowing them to put whatever
contents they like into the slot. The "read-only" cap is less powerful, only
granting read access, and not enabling modification of the data. The
read-write cap can be turned into the read-only cap, but not the other way
around.

The data in these files is distributed over a number of servers, using the
same erasure coding that immutable files use, with 3-of-10 being a typical
choice of encoding parameters. The data is encrypted and signed in such a way
that only the holders of the read-write cap will be able to set the contents
of the slot, and only the holders of the read-only cap will be able to read
those contents. Holders of either cap will be able to validate the contents
as being written by someone with the read-write cap. The servers who hold the
shares are not automatically given the ability read or modify them: the worst
they can do is deny service (by deleting or corrupting the shares), or
attempt a rollback attack (which can only succeed with the cooperation of at
least k servers).


Mutable Formats
===============

History
-------

When mutable files first shipped in Tahoe-0.8.0 (15-Feb-2008), the only
version available was "SDMF", described below. This was a
limited-functionality placeholder, intended to be replaced with
improved-efficiency "MDMF" files shortly afterwards. The development process
took longer than expected, and MDMF didn't ship until Tahoe-1.9.0
(31-Oct-2011), and even then it was opt-in (not used by default).

SDMF was intended for relatively small mutable files, up to a few megabytes.
It uses only one segment, so alacrity (the measure of how quickly the first
byte of plaintext is returned to the client) suffers, as the whole file must
be downloaded even if you only want to get a single byte. The memory used by
both clients and servers also scales with the size of the file, instead of
being limited to the half-a-MB-or-so that immutable file operations use, so
large files cause significant memory usage. To discourage the use of SDMF
outside it's design parameters, the early versions of Tahoe enforced a
maximum size on mutable files (maybe 10MB). Since most directories are built
out of mutable files, this imposed a limit of about 30k entries per
directory. In subsequent releases, this limit was removed, but the
performance problems inherent in the SDMF implementation remained.

In the summer of 2010, Google-Summer-of-Code student Kevan Carstensen took on
the project of finally implementing MDMF. Because of my (Brian) design
mistake in SDMF (not including a separate encryption seed in each segment),
the share format for SDMF could not be used for MDMF, resulting in a larger
gap between the two implementations (my original intention had been to make
SDMF a clean subset of MDMF, where any single-segment MDMF file could be
handled by the old SDMF code). In the fall of 2011, Kevan's code was finally
integrated, and first made available in the Tahoe-1.9.0 release.

SDMF vs. MDMF
-------------

The improvement of MDMF is the use of multiple segments: individual 128-KiB
sections of the file can be retrieved or modified independently. The
improvement can be seen when fetching just a portion of the file (using a
Range: header on the webapi), or when modifying a portion (again with a
Range: header). It can also be seen indirectly when fetching the whole file:
the first segment of data should be delivered faster from a large MDMF file
than from an SDMF file, although the overall download will then proceed at
the same rate.

We've decided to make it opt-in for now: mutable files default to
SDMF format unless explicitly configured to use MDMF, either in ``tahoe.cfg``
(see :doc:`../configuration`) or in the WUI or CLI command that created a
new mutable file.

The code can read and modify existing files of either format without user
intervention. We expect to make MDMF the default in a subsequent release,
perhaps 2.0.

Which format should you use? SDMF works well for files up to a few MB, and
can be handled by older versions (Tahoe-1.8.3 and earlier). If you do not
need to support older clients, want to efficiently work with mutable files,
and have code which will use Range: headers that make partial reads and
writes, then MDMF is for you.


Consistency vs. Availability
============================

There is an age-old battle between consistency and availability. Epic papers
have been written, elaborate proofs have been established, and generations of
theorists have learned that you cannot simultaneously achieve guaranteed
consistency with guaranteed reliability. In addition, the closer to 0 you get
on either axis, the cost and complexity of the design goes up.

Tahoe's design goals are to largely favor design simplicity, then slightly
favor read availability, over the other criteria.

As we develop more sophisticated mutable slots, the API may expose multiple
read versions to the application layer. The tahoe philosophy is to defer most
consistency recovery logic to the higher layers. Some applications have
effective ways to merge multiple versions, so inconsistency is not
necessarily a problem (i.e. directory nodes can usually merge multiple
"add child" operations).


The Prime Coordination Directive: "Don't Do That"
=================================================

The current rule for applications which run on top of Tahoe is "do not
perform simultaneous uncoordinated writes". That means you need non-tahoe
means to make sure that two parties are not trying to modify the same mutable
slot at the same time. For example:

* don't give the read-write URI to anyone else. Dirnodes in a private
  directory generally satisfy this case, as long as you don't use two
  clients on the same account at the same time
* if you give a read-write URI to someone else, stop using it yourself. An
  inbox would be a good example of this.
* if you give a read-write URI to someone else, call them on the phone
  before you write into it
* build an automated mechanism to have your agents coordinate writes.
  For example, we expect a future release to include a FURL for a
  "coordination server" in the dirnodes. The rule can be that you must
  contact the coordination server and obtain a lock/lease on the file
  before you're allowed to modify it.

If you do not follow this rule, Bad Things will happen. The worst-case Bad
Thing is that the entire file will be lost. A less-bad Bad Thing is that one
or more of the simultaneous writers will lose their changes. An observer of
the file may not see monotonically-increasing changes to the file, i.e. they
may see version 1, then version 2, then 3, then 2 again.

Tahoe takes some amount of care to reduce the badness of these Bad Things.
One way you can help nudge it from the "lose your file" case into the "lose
some changes" case is to reduce the number of competing versions: multiple
versions of the file that different parties are trying to establish as the
one true current contents. Each simultaneous writer counts as a "competing
version", as does the previous version of the file. If the count "S" of these
competing versions is larger than N/k, then the file runs the risk of being
lost completely. [TODO] If at least one of the writers remains running after
the collision is detected, it will attempt to recover, but if S>(N/k) and all
writers crash after writing a few shares, the file will be lost.

Note that Tahoe uses serialization internally to make sure that a single
Tahoe node will not perform simultaneous modifications to a mutable file. It
accomplishes this by using a weakref cache of the MutableFileNode (so that
there will never be two distinct MutableFileNodes for the same file), and by
forcing all mutable file operations to obtain a per-node lock before they
run. The Prime Coordination Directive therefore applies to inter-node
conflicts, not intra-node ones.


Small Distributed Mutable Files
===============================

SDMF slots are suitable for small (<1MB) files that are editing by rewriting
the entire file. The three operations are:

 * allocate (with initial contents)
 * set (with new contents)
 * get (old contents)

The first use of SDMF slots will be to hold directories (dirnodes), which map
encrypted child names to rw-URI/ro-URI pairs.

SDMF slots overview
-------------------

Each SDMF slot is created with a public/private key pair. The public key is
known as the "verification key", while the private key is called the
"signature key". The private key is hashed and truncated to 16 bytes to form
the "write key" (an AES symmetric key). The write key is then hashed and
truncated to form the "read key". The read key is hashed and truncated to
form the 16-byte "storage index" (a unique string used as an index to locate
stored data).

The public key is hashed by itself to form the "verification key hash".

The write key is hashed a different way to form the "write enabler master".
For each storage server on which a share is kept, the write enabler master is
concatenated with the server's nodeid and hashed, and the result is called
the "write enabler" for that particular server. Note that multiple shares of
the same slot stored on the same server will all get the same write enabler,
i.e. the write enabler is associated with the "bucket", rather than the
individual shares.

The private key is encrypted (using AES in counter mode) by the write key,
and the resulting crypttext is stored on the servers. so it will be
retrievable by anyone who knows the write key. The write key is not used to
encrypt anything else, and the private key never changes, so we do not need
an IV for this purpose.

The actual data is encrypted (using AES in counter mode) with a key derived
by concatenating the readkey with the IV, the hashing the results and
truncating to 16 bytes. The IV is randomly generated each time the slot is
updated, and stored next to the encrypted data.

The read-write URI consists of the write key and the verification key hash.
The read-only URI contains the read key and the verification key hash. The
verify-only URI contains the storage index and the verification key hash.

::

 URI:SSK-RW:b2a(writekey):b2a(verification_key_hash)
 URI:SSK-RO:b2a(readkey):b2a(verification_key_hash)
 URI:SSK-Verify:b2a(storage_index):b2a(verification_key_hash)

Note that this allows the read-only and verify-only URIs to be derived from
the read-write URI without actually retrieving the public keys. Also note
that it means the read-write agent must validate both the private key and the
public key when they are first fetched. All users validate the public key in
exactly the same way.

The SDMF slot is allocated by sending a request to the storage server with a
desired size, the storage index, and the write enabler for that server's
nodeid. If granted, the write enabler is stashed inside the slot's backing
store file. All further write requests must be accompanied by the write
enabler or they will not be honored. The storage server does not share the
write enabler with anyone else.

The SDMF slot structure will be described in more detail below. The important
pieces are:

* a sequence number
* a root hash "R"
* the encoding parameters (including k, N, file size, segment size)
* a signed copy of [seqnum,R,encoding_params], using the signature key
* the verification key (not encrypted)
* the share hash chain (part of a Merkle tree over the share hashes)
* the block hash tree (Merkle tree over blocks of share data)
* the share data itself (erasure-coding of read-key-encrypted file data)
* the signature key, encrypted with the write key

The access pattern for read is:

* hash read-key to get storage index
* use storage index to locate 'k' shares with identical 'R' values

  * either get one share, read 'k' from it, then read k-1 shares
  * or read, say, 5 shares, discover k, either get more or be finished
  * or copy k into the URIs

* read verification key
* hash verification key, compare against verification key hash
* read seqnum, R, encoding parameters, signature
* verify signature against verification key
* read share data, compute block-hash Merkle tree and root "r"
* read share hash chain (leading from "r" to "R")
* validate share hash chain up to the root "R"
* submit share data to erasure decoding
* decrypt decoded data with read-key
* submit plaintext to application

The access pattern for write is:

* hash write-key to get read-key, hash read-key to get storage index
* use the storage index to locate at least one share
* read verification key and encrypted signature key
* decrypt signature key using write-key
* hash signature key, compare against write-key
* hash verification key, compare against verification key hash
* encrypt plaintext from application with read-key

  * application can encrypt some data with the write-key to make it only
    available to writers (use this for transitive read-onlyness of dirnodes)

* erasure-code crypttext to form shares
* split shares into blocks
* compute Merkle tree of blocks, giving root "r" for each share
* compute Merkle tree of shares, find root "R" for the file as a whole
* create share data structures, one per server:

  * use seqnum which is one higher than the old version
  * share hash chain has log(N) hashes, different for each server
  * signed data is the same for each server

* now we have N shares and need homes for them
* walk through peers

  * if share is not already present, allocate-and-set
  * otherwise, try to modify existing share:
  * send testv_and_writev operation to each one
  * testv says to accept share if their(seqnum+R) <= our(seqnum+R)
  * count how many servers wind up with which versions (histogram over R)
  * keep going until N servers have the same version, or we run out of servers

    * if any servers wound up with a different version, report error to
      application
    * if we ran out of servers, initiate recovery process (described below)

Server Storage Protocol
-----------------------

The storage servers will provide a mutable slot container which is oblivious
to the details of the data being contained inside it. Each storage index
refers to a "bucket", and each bucket has one or more shares inside it. (In a
well-provisioned network, each bucket will have only one share). The bucket
is stored as a directory, using the base32-encoded storage index as the
directory name. Each share is stored in a single file, using the share number
as the filename.

The container holds space for a container magic number (for versioning), the
write enabler, the nodeid which accepted the write enabler (used for share
migration, described below), a small number of lease structures, the embedded
data itself, and expansion space for additional lease structures::

 #   offset    size    name
 1   0         32      magic verstr "Tahoe mutable container v1\n\x75\x09\x44\x03\x8e"
 2   32        20      write enabler's nodeid
 3   52        32      write enabler
 4   84        8       data size (actual share data present) (a)
 5   92        8       offset of (8) count of extra leases (after data)
 6   100       368     four leases, 92 bytes each
                        0    4   ownerid (0 means "no lease here")
                        4    4   expiration timestamp
                        8   32   renewal token
                        40  32   cancel token
                        72  20   nodeid which accepted the tokens
 7   468       (a)     data
 8   ??        4       count of extra leases
 9   ??        n*92    extra leases

The "extra leases" field must be copied and rewritten each time the size of
the enclosed data changes. The hope is that most buckets will have four or
fewer leases and this extra copying will not usually be necessary.

The (4) "data size" field contains the actual number of bytes of data present
in field (7), such that a client request to read beyond 504+(a) will result
in an error. This allows the client to (one day) read relative to the end of
the file. The container size (that is, (8)-(7)) might be larger, especially
if extra size was pre-allocated in anticipation of filling the container with
a lot of data.

The offset in (5) points at the *count* of extra leases, at (8). The actual
leases (at (9)) begin 4 bytes later. If the container size changes, both (8)
and (9) must be relocated by copying.

The server will honor any write commands that provide the write token and do
not exceed the server-wide storage size limitations. Read and write commands
MUST be restricted to the 'data' portion of the container: the implementation
of those commands MUST perform correct bounds-checking to make sure other
portions of the container are inaccessible to the clients.

The two methods provided by the storage server on these "MutableSlot" share
objects are:

* readv(ListOf(offset=int, length=int))

  * returns a list of bytestrings, of the various requested lengths
  * offset < 0 is interpreted relative to the end of the data
  * spans which hit the end of the data will return truncated data

* testv_and_writev(write_enabler, test_vector, write_vector)

  * this is a test-and-set operation which performs the given tests and only
    applies the desired writes if all tests succeed. This is used to detect
    simultaneous writers, and to reduce the chance that an update will lose
    data recently written by some other party (written after the last time
    this slot was read).
  * test_vector=ListOf(TupleOf(offset, length, opcode, specimen))
  * the opcode is a string, from the set [gt, ge, eq, le, lt, ne]
  * each element of the test vector is read from the slot's data and 
    compared against the specimen using the desired (in)equality. If all
    tests evaluate True, the write is performed
  * write_vector=ListOf(TupleOf(offset, newdata))

    * offset < 0 is not yet defined, it probably means relative to the
      end of the data, which probably means append, but we haven't nailed
      it down quite yet
    * write vectors are executed in order, which specifies the results of
      overlapping writes

  * return value:

    * error: OutOfSpace
    * error: something else (io error, out of memory, whatever)
    * (True, old_test_data): the write was accepted (test_vector passed)
    * (False, old_test_data): the write was rejected (test_vector failed)

      * both 'accepted' and 'rejected' return the old data that was used
        for the test_vector comparison. This can be used by the client
        to detect write collisions, including collisions for which the
        desired behavior was to overwrite the old version.

In addition, the storage server provides several methods to access these
share objects:

* allocate_mutable_slot(storage_index, sharenums=SetOf(int))

  * returns DictOf(int, MutableSlot)

* get_mutable_slot(storage_index)

  * returns DictOf(int, MutableSlot)
  * or raises KeyError

We intend to add an interface which allows small slots to allocate-and-write
in a single call, as well as do update or read in a single call. The goal is
to allow a reasonably-sized dirnode to be created (or updated, or read) in
just one round trip (to all N shareholders in parallel).

migrating shares
````````````````

If a share must be migrated from one server to another, two values become
invalid: the write enabler (since it was computed for the old server), and
the lease renew/cancel tokens.

Suppose that a slot was first created on nodeA, and was thus initialized with
WE(nodeA) (= H(WEM+nodeA)). Later, for provisioning reasons, the share is
moved from nodeA to nodeB.

Readers may still be able to find the share in its new home, depending upon
how many servers are present in the grid, where the new nodeid lands in the
permuted index for this particular storage index, and how many servers the
reading client is willing to contact.

When a client attempts to write to this migrated share, it will get a "bad
write enabler" error, since the WE it computes for nodeB will not match the
WE(nodeA) that was embedded in the share. When this occurs, the "bad write
enabler" message must include the old nodeid (e.g. nodeA) that was in the
share.

The client then computes H(nodeB+H(WEM+nodeA)), which is the same as
H(nodeB+WE(nodeA)). The client sends this along with the new WE(nodeB), which
is H(WEM+nodeB). Note that the client only sends WE(nodeB) to nodeB, never to
anyone else. Also note that the client does not send a value to nodeB that
would allow the node to impersonate the client to a third node: everything
sent to nodeB will include something specific to nodeB in it.

The server locally computes H(nodeB+WE(nodeA)), using its own node id and the
old write enabler from the share. It compares this against the value supplied
by the client. If they match, this serves as proof that the client was able
to compute the old write enabler. The server then accepts the client's new
WE(nodeB) and writes it into the container.

This WE-fixup process requires an extra round trip, and requires the error
message to include the old nodeid, but does not require any public key
operations on either client or server.

Migrating the leases will require a similar protocol. This protocol will be
defined concretely at a later date.

Code Details
------------

The MutableFileNode class is used to manipulate mutable files (as opposed to
ImmutableFileNodes). These are initially generated with
client.create_mutable_file(), and later recreated from URIs with
client.create_node_from_uri(). Instances of this class will contain a URI and
a reference to the client (for peer selection and connection).

NOTE: this section is out of date. Please see src/allmydata/interfaces.py
(the section on IMutableFilesystemNode) for more accurate information.

The methods of MutableFileNode are:

* download_to_data() -> [deferred] newdata, NotEnoughSharesError

  * if there are multiple retrieveable versions in the grid, get() returns
    the first version it can reconstruct, and silently ignores the others.
    In the future, a more advanced API will signal and provide access to
    the multiple heads.

* update(newdata) -> OK, UncoordinatedWriteError, NotEnoughSharesError
* overwrite(newdata) -> OK, UncoordinatedWriteError, NotEnoughSharesError

download_to_data() causes a new retrieval to occur, pulling the current
contents from the grid and returning them to the caller. At the same time,
this call caches information about the current version of the file. This
information will be used in a subsequent call to update(), and if another
change has occured between the two, this information will be out of date,
triggering the UncoordinatedWriteError.

update() is therefore intended to be used just after a download_to_data(), in
the following pattern::

 d = mfn.download_to_data()
 d.addCallback(apply_delta)
 d.addCallback(mfn.update)

If the update() call raises UCW, then the application can simply return an
error to the user ("you violated the Prime Coordination Directive"), and they
can try again later. Alternatively, the application can attempt to retry on
its own. To accomplish this, the app needs to pause, download the new
(post-collision and post-recovery) form of the file, reapply their delta,
then submit the update request again. A randomized pause is necessary to
reduce the chances of colliding a second time with another client that is
doing exactly the same thing::

 d = mfn.download_to_data()
 d.addCallback(apply_delta)
 d.addCallback(mfn.update)
 def _retry(f):
   f.trap(UncoordinatedWriteError)
   d1 = pause(random.uniform(5, 20))
   d1.addCallback(lambda res: mfn.download_to_data())
   d1.addCallback(apply_delta)
   d1.addCallback(mfn.update)
   return d1
 d.addErrback(_retry)

Enthusiastic applications can retry multiple times, using a randomized
exponential backoff between each. A particularly enthusiastic application can
retry forever, but such apps are encouraged to provide a means to the user of
giving up after a while.

UCW does not mean that the update was not applied, so it is also a good idea
to skip the retry-update step if the delta was already applied::

 d = mfn.download_to_data()
 d.addCallback(apply_delta)
 d.addCallback(mfn.update)
 def _retry(f):
   f.trap(UncoordinatedWriteError)
   d1 = pause(random.uniform(5, 20))
   d1.addCallback(lambda res: mfn.download_to_data())
   def _maybe_apply_delta(contents):
     new_contents = apply_delta(contents)
     if new_contents != contents:
       return mfn.update(new_contents)
   d1.addCallback(_maybe_apply_delta)
   return d1
 d.addErrback(_retry)

update() is the right interface to use for delta-application situations, like
directory nodes (in which apply_delta might be adding or removing child
entries from a serialized table).

Note that any uncoordinated write has the potential to lose data. We must do
more analysis to be sure, but it appears that two clients who write to the
same mutable file at the same time (even if both eventually retry) will, with
high probability, result in one client observing UCW and the other silently
losing their changes. It is also possible for both clients to observe UCW.
The moral of the story is that the Prime Coordination Directive is there for
a reason, and that recovery/UCW/retry is not a subsitute for write
coordination.

overwrite() tells the client to ignore this cached version information, and
to unconditionally replace the mutable file's contents with the new data.
This should not be used in delta application, but rather in situations where
you want to replace the file's contents with completely unrelated ones. When
raw files are uploaded into a mutable slot through the Tahoe-LAFS web-API
(using POST and the ?mutable=true argument), they are put in place with
overwrite().

The peer-selection and data-structure manipulation (and signing/verification)
steps will be implemented in a separate class in allmydata/mutable.py .

SMDF Slot Format
----------------

This SMDF data lives inside a server-side MutableSlot container. The server
is oblivious to this format.

This data is tightly packed. In particular, the share data is defined to run
all the way to the beginning of the encrypted private key (the encprivkey
offset is used both to terminate the share data and to begin the encprivkey).

::

  #    offset   size    name
  1    0        1       version byte, \x00 for this format
  2    1        8       sequence number. 2^64-1 must be handled specially, TBD
  3    9        32      "R" (root of share hash Merkle tree)
  4    41       16      IV (share data is AES(H(readkey+IV)) )
  5    57       18      encoding parameters:
        57       1        k
        58       1        N
        59       8        segment size
        67       8        data length (of original plaintext)
  6    75       32      offset table:
        75       4        (8) signature
        79       4        (9) share hash chain
        83       4        (10) block hash tree
        87       4        (11) share data
        91       8        (12) encrypted private key
        99       8        (13) EOF
  7    107      436ish  verification key (2048 RSA key)
  8    543ish   256ish  signature=RSAsign(sigkey, H(version+seqnum+r+IV+encparm))
  9    799ish   (a)     share hash chain, encoded as:
                         "".join([pack(">H32s", shnum, hash)
                                  for (shnum,hash) in needed_hashes])
 10    (927ish) (b)     block hash tree, encoded as:
                         "".join([pack(">32s",hash) for hash in block_hash_tree])
 11    (935ish) LEN     share data (no gap between this and encprivkey)
 12    ??       1216ish encrypted private key= AESenc(write-key, RSA-key)
 13    ??       --      EOF

 (a) The share hash chain contains ceil(log(N)) hashes, each 32 bytes long.
    This is the set of hashes necessary to validate this share's leaf in the
    share Merkle tree. For N=10, this is 4 hashes, i.e. 128 bytes.
 (b) The block hash tree contains ceil(length/segsize) hashes, each 32 bytes
    long. This is the set of hashes necessary to validate any given block of
    share data up to the per-share root "r". Each "r" is a leaf of the share
    has tree (with root "R"), from which a minimal subset of hashes is put in
    the share hash chain in (8).

Recovery
--------

The first line of defense against damage caused by colliding writes is the
Prime Coordination Directive: "Don't Do That".

The second line of defense is to keep "S" (the number of competing versions)
lower than N/k. If this holds true, at least one competing version will have
k shares and thus be recoverable. Note that server unavailability counts
against us here: the old version stored on the unavailable server must be
included in the value of S.

The third line of defense is our use of testv_and_writev() (described below),
which increases the convergence of simultaneous writes: one of the writers
will be favored (the one with the highest "R"), and that version is more
likely to be accepted than the others. This defense is least effective in the
pathological situation where S simultaneous writers are active, the one with
the lowest "R" writes to N-k+1 of the shares and then dies, then the one with
the next-lowest "R" writes to N-2k+1 of the shares and dies, etc, until the
one with the highest "R" writes to k-1 shares and dies. Any other sequencing
will allow the highest "R" to write to at least k shares and establish a new
revision.

The fourth line of defense is the fact that each client keeps writing until
at least one version has N shares. This uses additional servers, if
necessary, to make sure that either the client's version or some
newer/overriding version is highly available.

The fifth line of defense is the recovery algorithm, which seeks to make sure
that at least *one* version is highly available, even if that version is
somebody else's.

The write-shares-to-peers algorithm is as follows:

* permute peers according to storage index
* walk through peers, trying to assign one share per peer
* for each peer:

  * send testv_and_writev, using "old(seqnum+R) <= our(seqnum+R)" as the test

    * this means that we will overwrite any old versions, and we will
      overwrite simultaenous writers of the same version if our R is higher.
      We will not overwrite writers using a higher seqnum.

  * record the version that each share winds up with. If the write was
    accepted, this is our own version. If it was rejected, read the
    old_test_data to find out what version was retained.
  * if old_test_data indicates the seqnum was equal or greater than our
    own, mark the "Simultanous Writes Detected" flag, which will eventually
    result in an error being reported to the writer (in their close() call).
  * build a histogram of "R" values
  * repeat until the histogram indicate that some version (possibly ours)
    has N shares. Use new servers if necessary.
  * If we run out of servers:

    * if there are at least shares-of-happiness of any one version, we're
      happy, so return. (the close() might still get an error)
    * not happy, need to reinforce something, goto RECOVERY

Recovery:

* read all shares, count the versions, identify the recoverable ones,
  discard the unrecoverable ones.
* sort versions: locate max(seqnums), put all versions with that seqnum
  in the list, sort by number of outstanding shares. Then put our own
  version. (TODO: put versions with seqnum <max but >us ahead of us?).
* for each version:

  * attempt to recover that version
  * if not possible, remove it from the list, go to next one
  * if recovered, start at beginning of peer list, push that version,
    continue until N shares are placed
  * if pushing our own version, bump up the seqnum to one higher than
    the max seqnum we saw
  * if we run out of servers:

    * schedule retry and exponential backoff to repeat RECOVERY

  * admit defeat after some period? presumeably the client will be shut down
    eventually, maybe keep trying (once per hour?) until then.


Medium Distributed Mutable Files
================================

These are just like the SDMF case, but:

* We actually take advantage of the Merkle hash tree over the blocks, by
  reading a single segment of data at a time (and its necessary hashes), to
  reduce the read-time alacrity.
* We allow arbitrary writes to any range of the file.
* We add more code to first read each segment that a write must modify.
  This looks exactly like the way a normal filesystem uses a block device,
  or how a CPU must perform a cache-line fill before modifying a single word.
* We might implement some sort of copy-based atomic update server call,
  to allow multiple writev() calls to appear atomic to any readers.

MDMF slots provide fairly efficient in-place edits of very large files (a few
GB). Appending data is also fairly efficient.


Large Distributed Mutable Files
===============================

LDMF slots (not implemented) would use a fundamentally different way to store
the file, inspired by Mercurial's "revlog" format. This would enable very
efficient insert/remove/replace editing of arbitrary spans. Multiple versions
of the file can be retained, in a revision graph that can have multiple heads.
Each revision can be referenced by a cryptographic identifier. There are two
forms of the URI, one that means "most recent version", and a longer one that
points to a specific revision.

Metadata can be attached to the revisions, like timestamps, to enable rolling
back an entire tree to a specific point in history.

LDMF1 provides deltas but tries to avoid dealing with multiple heads. LDMF2
provides explicit support for revision identifiers and branching.


TODO
====

improve allocate-and-write or get-writer-buckets API to allow one-call (or
maybe two-call) updates. The challenge is in figuring out which shares are on
which machines. First cut will have lots of round trips.

(eventually) define behavior when seqnum wraps. At the very least make sure
it can't cause a security problem. "the slot is worn out" is acceptable.

(eventually) define share-migration lease update protocol. Including the
nodeid who accepted the lease is useful, we can use the same protocol as we
do for updating the write enabler. However we need to know which lease to
update.. maybe send back a list of all old nodeids that we find, then try all
of them when we accept the update?

We now do this in a specially-formatted IndexError exception:
 "UNABLE to renew non-existent lease. I have leases accepted by " +
 "nodeids: '12345','abcde','44221' ."

confirm that a repairer can regenerate shares without the private key. Hmm,
without the write-enabler they won't be able to write those shares to the
servers.. although they could add immutable new shares to new servers.
