﻿.. -*- coding: utf-8-with-signature -*-

=======================
Tahoe-LAFS Architecture
=======================

1.  `Overview`_
2.  `The Key-Value Store`_
3.  `File Encoding`_
4.  `Capabilities`_
5.  `Server Selection`_
6.  `Swarming Download, Trickling Upload`_
7.  `The File Store Layer`_
8.  `Leases, Refreshing, Garbage Collection`_
9.  `File Repairer`_
10. `Security`_
11. `Reliability`_


Overview
========

(See the `docs/specifications directory`_ for more details.)

There are three layers: the key-value store, the file store, and the
application.

The lowest layer is the key-value store. The keys are "capabilities" -- short
ASCII strings -- and the values are sequences of data bytes. This data is
encrypted and distributed across a number of nodes, such that it will survive
the loss of most of the nodes. There are no hard limits on the size of the
values, but there may be performance issues with extremely large values (just
due to the limitation of network bandwidth). In practice, values as small as
a few bytes and as large as tens of gigabytes are in common use.

The middle layer is the decentralized file store: a directed graph in which
the intermediate nodes are directories and the leaf nodes are files. The leaf
nodes contain only the data -- they contain no metadata other than the length
in bytes. The edges leading to leaf nodes have metadata attached to them
about the file they point to. Therefore, the same file may be associated with
different metadata if it is referred to through different edges.

The top layer consists of the applications using the file store.
Allmydata.com used it for a backup service: the application periodically
copies files from the local disk onto the decentralized file store. We later
provide read-only access to those files, allowing users to recover them.
There are several other applications built on top of the Tahoe-LAFS
file store (see the RelatedProjects_ page of the wiki for a list).

.. _docs/specifications directory: https://github.com/tahoe-lafs/tahoe-lafs/tree/master/docs/specifications
.. _RelatedProjects: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/RelatedProjects

The Key-Value Store
===================

The key-value store is implemented by a grid of Tahoe-LAFS storage servers --
user-space processes. Tahoe-LAFS storage clients communicate with the storage
servers over TCP.

Storage servers hold data in the form of "shares". Shares are encoded pieces
of files. There are a configurable number of shares for each file, 10 by
default. Normally, each share is stored on a separate server, but in some
cases a single server can hold multiple shares of a file.

Nodes learn about each other through an "introducer". Each server connects to
the introducer at startup and announces its presence. Each client connects to
the introducer at startup, and receives a list of all servers from it. Each
client then connects to every server, creating a "bi-clique" topology. In the
current release, nodes behind NAT boxes will connect to all nodes that they
can open connections to, but they cannot open connections to other nodes
behind NAT boxes. Therefore, the more nodes behind NAT boxes, the less the
topology resembles the intended bi-clique topology.

The introducer is a Single Point of Failure ("SPoF"), in that clients who
never connect to the introducer will be unable to connect to any storage
servers, but once a client has been introduced to everybody, it does not need
the introducer again until it is restarted. The danger of a SPoF is further
reduced in two ways. First, the introducer is defined by a hostname and a
private key, which are easy to move to a new host in case the original one
suffers an unrecoverable hardware problem. Second, even if the private key is
lost, clients can be reconfigured to use a new introducer.

For future releases, we have plans to decentralize introduction, allowing any
server to tell a new client about all the others.


File Encoding
=============

When a client stores a file on the grid, it first encrypts the file. It then
breaks the encrypted file into small segments, in order to reduce the memory
footprint, and to decrease the lag between initiating a download and
receiving the first part of the file; for example the lag between hitting
"play" and a movie actually starting.

The client then erasure-codes each segment, producing blocks of which only a
subset are needed to reconstruct the segment (3 out of 10, with the default
settings).

It sends one block from each segment to a given server. The set of blocks on
a given server constitutes a "share". Therefore a subset of the shares (3 out
of 10, by default) are needed to reconstruct the file.

A hash of the encryption key is used to form the "storage index", which is
used for both server selection (described below) and to index shares within
the Storage Servers on the selected nodes.

The client computes secure hashes of the ciphertext and of the shares. It
uses `Merkle Trees`_ so that it is possible to verify the correctness of a
subset of the data without requiring all of the data. For example, this
allows you to verify the correctness of the first segment of a movie file and
then begin playing the movie file in your movie viewer before the entire
movie file has been downloaded.

These hashes are stored in a small datastructure named the Capability
Extension Block which is stored on the storage servers alongside each share.

The capability contains the encryption key, the hash of the Capability
Extension Block, and any encoding parameters necessary to perform the
eventual decoding process. For convenience, it also contains the size of the
file being stored.

To download, the client that wishes to turn a capability into a sequence of
bytes will obtain the blocks from storage servers, use erasure-decoding to
turn them into segments of ciphertext, use the decryption key to convert that
into plaintext, then emit the plaintext bytes to the output target.

.. _`Merkle Trees`: http://systems.cs.colorado.edu/grunwald/Classes/Fall2003-InformationStorage/Papers/merkle-tree.pdf


Capabilities
============

Capabilities to immutable files represent a specific set of bytes. Think of
it like a hash function: you feed in a bunch of bytes, and you get out a
capability, which is deterministically derived from the input data: changing
even one bit of the input data will result in a completely different
capability.

Read-only capabilities to mutable files represent the ability to get a set of
bytes representing some version of the file, most likely the latest version.
Each read-only capability is unique. In fact, each mutable file has a unique
public/private key pair created when the mutable file is created, and the
read-only capability to that file includes a secure hash of the public key.

Read-write capabilities to mutable files represent the ability to read the
file (just like a read-only capability) and also to write a new version of
the file, overwriting any extant version. Read-write capabilities are unique
-- each one includes the secure hash of the private key associated with that
mutable file.

The capability provides both "location" and "identification": you can use it
to retrieve a set of bytes, and then you can use it to validate ("identify")
that these potential bytes are indeed the ones that you were looking for.

The "key-value store" layer doesn't include human-meaningful names.
Capabilities sit on the "global+secure" edge of `Zooko's Triangle`_. They are
self-authenticating, meaning that nobody can trick you into accepting a file
that doesn't match the capability you used to refer to that file. The
file store layer (described below) adds human-meaningful names atop the
key-value layer.

.. _`Zooko's Triangle`: https://en.wikipedia.org/wiki/Zooko%27s_triangle


Server Selection
================

When a file is uploaded, the encoded shares are sent to some servers. But to
which ones? The "server selection" algorithm is used to make this choice.

The storage index is used to consistently-permute the set of all servers nodes
(by sorting them by ``HASH(storage_index+nodeid)``). Each file gets a different
permutation, which (on average) will evenly distribute shares among the grid
and avoid hotspots. Each server has announced its available space when it
connected to the introducer, and we use that available space information to
remove any servers that cannot hold an encoded share for our file. Then we ask
some of the servers thus removed if they are already holding any encoded shares
for our file; we use this information later. (We ask any servers which are in
the first 2*``N`` elements of the permuted list.)

We then use the permuted list of servers to ask each server, in turn, if it
will hold a share for us (a share that was not reported as being already
present when we talked to the full servers earlier, and that we have not
already planned to upload to a different server). We plan to send a share to a
server by sending an 'allocate_buckets() query' to the server with the number
of that share. Some will say yes they can hold that share, others (those who
have become full since they announced their available space) will say no; when
a server refuses our request, we take that share to the next server on the
list. In the response to allocate_buckets() the server will also inform us of
any shares of that file that it already has. We keep going until we run out of
shares that need to be stored. At the end of the process, we'll have a table
that maps each share number to a server, and then we can begin the encode and
push phase, using the table to decide where each share should be sent.

Most of the time, this will result in one share per server, which gives us
maximum reliability.  If there are fewer writable servers than there are
unstored shares, we'll be forced to loop around, eventually giving multiple
shares to a single server.

If we have to loop through the node list a second time, we accelerate the query
process, by asking each node to hold multiple shares on the second pass. In
most cases, this means we'll never send more than two queries to any given
node.

If a server is unreachable, or has an error, or refuses to accept any of our
shares, we remove it from the permuted list, so we won't query it again for
this file. If a server already has shares for the file we're uploading, we add
that information to the share-to-server table. This lets us do less work for
files which have been uploaded once before, while making sure we still wind up
with as many shares as we desire.

Before a file upload is called successful, it has to pass an upload health
check. For immutable files, we check to see that a condition called
'servers-of-happiness' is satisfied. When satisfied, 'servers-of-happiness'
assures us that enough pieces of the file are distributed across enough
servers on the grid to ensure that the availability of the file will not be
affected if a few of those servers later fail. For mutable files and
directories, we check to see that all of the encoded shares generated during
the upload process were successfully placed on the grid. This is a weaker
check than 'servers-of-happiness'; it does not consider any information about
how the encoded shares are placed on the grid, and cannot detect situations in
which all or a majority of the encoded shares generated during the upload
process reside on only one storage server. We hope to extend
'servers-of-happiness' to mutable files in a future release of Tahoe-LAFS. If,
at the end of the upload process, the appropriate upload health check fails,
the upload is considered a failure.

The current defaults use ``k`` = 3, ``servers_of_happiness`` = 7, and ``N`` = 10.
``N`` = 10 means that we'll try to place 10 shares. ``k`` = 3 means that we need
any three shares to recover the file. ``servers_of_happiness`` = 7 means that
we'll consider an immutable file upload to be successful if we can place shares
on enough servers that there are 7 different servers, the correct functioning
of any ``k`` of which guarantee the availability of the immutable file.

``N`` = 10 and ``k`` = 3 means there is a 3.3x expansion factor. On a small grid, you
should set ``N`` about equal to the number of storage servers in your grid; on a
large grid, you might set it to something smaller to avoid the overhead of
contacting every server to place a file. In either case, you should then set ``k``
such that ``N``/``k`` reflects your desired availability goals. The best value for
``servers_of_happiness`` will depend on how you use Tahoe-LAFS. In a friendnet
with a variable number of servers, it might make sense to set it to the smallest
number of servers that you expect to have online and accepting shares at any
given time. In a stable environment without much server churn, it may make
sense to set ``servers_of_happiness`` = ``N``.

When downloading a file, the current version just asks all known servers for
any shares they might have. Once it has received enough responses that it
knows where to find the needed k shares, it downloads at least the first
segment from those servers. This means that it tends to download shares from
the fastest servers. If some servers had more than one share, it will continue
sending "Do You Have Block" requests to other servers, so that it can download
subsequent segments from distinct servers (sorted by their DYHB round-trip
times), if possible.

  *future work*

  A future release will use the server selection algorithm to reduce the
  number of queries that must be sent out.

  Other peer-node selection algorithms are possible. One earlier version
  (known as "Tahoe 3") used the permutation to place the nodes around a large
  ring, distributed the shares evenly around the same ring, then walked
  clockwise from 0 with a basket. Each time it encountered a share, it put it
  in the basket, each time it encountered a server, give it as many shares
  from the basket as they'd accept. This reduced the number of queries
  (usually to 1) for small grids (where ``N`` is larger than the number of
  nodes), but resulted in extremely non-uniform share distribution, which
  significantly hurt reliability (sometimes the permutation resulted in most
  of the shares being dumped on a single node).

  Another algorithm (known as "denver airport" [#naming]_) uses the permuted hash to
  decide on an approximate target for each share, then sends lease requests
  via Chord routing. The request includes the contact information of the
  uploading node, and asks that the node which eventually accepts the lease
  should contact the uploader directly. The shares are then transferred over
  direct connections rather than through multiple Chord hops. Download uses
  the same approach. This allows nodes to avoid maintaining a large number of
  long-term connections, at the expense of complexity and latency.

.. [#naming]  all of these names are derived from the location where they were
        concocted, in this case in a car ride from Boulder to DEN. To be
        precise, "Tahoe 1" was an unworkable scheme in which everyone who holds
        shares for a given file would form a sort of cabal which kept track of
        all the others, "Tahoe 2" is the first-100-nodes in the permuted hash
        described in this document, and "Tahoe 3" (or perhaps "Potrero hill 1")
        was the abandoned ring-with-many-hands approach.


Swarming Download, Trickling Upload
===================================

Because the shares being downloaded are distributed across a large number of
nodes, the download process will pull from many of them at the same time. The
current encoding parameters require 3 shares to be retrieved for each
segment, which means that up to 3 nodes will be used simultaneously. For
larger networks, 8-of-22 encoding could be used, meaning 8 nodes can be used
simultaneously. This allows the download process to use the sum of the
available nodes' upload bandwidths, resulting in downloads that take full
advantage of the common 8x disparity between download and upload bandwith on
modern ADSL lines.

On the other hand, uploads are hampered by the need to upload encoded shares
that are larger than the original data (3.3x larger with the current default
encoding parameters), through the slow end of the asymmetric connection. This
means that on a typical 8x ADSL line, uploading a file will take about 32
times longer than downloading it again later.

Smaller expansion ratios can reduce this upload penalty, at the expense of
reliability (see `Reliability`_, below). By using an "upload helper", this
penalty is eliminated: the client does a 1x upload of encrypted data to the
helper, then the helper performs encoding and pushes the shares to the
storage servers. This is an improvement if the helper has significantly
higher upload bandwidth than the client, so it makes the most sense for a
commercially-run grid for which all of the storage servers are in a colo
facility with high interconnect bandwidth. In this case, the helper is placed
in the same facility, so the helper-to-storage-server bandwidth is huge.

See :doc:`helper` for details about the upload helper.


The File Store Layer
====================

The "file store" layer is responsible for mapping human-meaningful pathnames
(directories and filenames) to pieces of data. The actual bytes inside these
files are referenced by capability, but the file store layer is where the
directory names, file names, and metadata are kept.

The file store layer is a graph of directories. Each directory contains a
table of named children. These children are either other directories or
files. All children are referenced by their capability.

A directory has two forms of capability: read-write caps and read-only caps.
The table of children inside the directory has a read-write and read-only
capability for each child. If you have a read-only capability for a given
directory, you will not be able to access the read-write capability of its
children. This results in "transitively read-only" directory access.

By having two different capabilities, you can choose which you want to share
with someone else. If you create a new directory and share the read-write
capability for it with a friend, then you will both be able to modify its
contents. If instead you give them the read-only capability, then they will
*not* be able to modify the contents. Any capability that you receive can be
linked in to any directory that you can modify, so very powerful
shared+published directory structures can be built from these components.

This structure enable individual users to have their own personal space, with
links to spaces that are shared with specific other users, and other spaces
that are globally visible.


Leases, Refreshing, Garbage Collection
======================================

When a file or directory in the file store is no longer referenced, the space
that its shares occupied on each storage server can be freed, making room for
other shares. Tahoe-LAFS uses a garbage collection ("GC") mechanism to
implement this space-reclamation process. Each share has one or more
"leases", which are managed by clients who want the file/directory to be
retained. The storage server accepts each share for a pre-defined period of
time, and is allowed to delete the share if all of the leases are cancelled
or allowed to expire.

Garbage collection is not enabled by default: storage servers will not delete
shares without being explicitly configured to do so. When GC is enabled,
clients are responsible for renewing their leases on a periodic basis at
least frequently enough to prevent any of the leases from expiring before the
next renewal pass.

See :doc:`garbage-collection` for further information, and for how to
configure garbage collection.

File Repairer
=============

Shares may go away because the storage server hosting them has suffered a
failure: either temporary downtime (affecting availability of the file), or a
permanent data loss (affecting the preservation of the file). Hard drives
crash, power supplies explode, coffee spills, and asteroids strike. The goal
of a robust distributed file store is to survive these setbacks.

To work against this slow, continual loss of shares, a File Checker is used
to periodically count the number of shares still available for any given
file. A more extensive form of checking known as the File Verifier can
download the ciphertext of the target file and perform integrity checks
(using strong hashes) to make sure the data is still intact. When the file is
found to have decayed below some threshold, the File Repairer can be used to
regenerate and re-upload the missing shares. These processes are conceptually
distinct (the repairer is only run if the checker/verifier decides it is
necessary), but in practice they will be closely related, and may run in the
same process.

The repairer process does not get the full capability of the file to be
maintained: it merely gets the "repairer capability" subset, which does not
include the decryption key. The File Verifier uses that data to find out
which nodes ought to hold shares for this file, and to see if those nodes are
still around and willing to provide the data. If the file is not healthy
enough, the File Repairer is invoked to download the ciphertext, regenerate
any missing shares, and upload them to new nodes. The goal of the File
Repairer is to finish up with a full set of ``N`` shares.

There are a number of engineering issues to be resolved here. The bandwidth,
disk IO, and CPU time consumed by the verification/repair process must be
balanced against the robustness that it provides to the grid. The nodes
involved in repair will have very different access patterns than normal
nodes, such that these processes may need to be run on hosts with more memory
or network connectivity than usual. The frequency of repair will directly
affect the resources consumed. In some cases, verification of multiple files
can be performed at the same time, and repair of files can be delegated off
to other nodes.

  *future work*

  Currently there are two modes of checking on the health of your file:
  "Checker" simply asks storage servers which shares they have and does
  nothing to try to verify that they aren't lying. "Verifier" downloads and
  cryptographically verifies every bit of every share of the file from every
  server, which costs a lot of network and CPU. A future improvement would be
  to make a random-sampling verifier which downloads and cryptographically
  verifies only a few randomly-chosen blocks from each server. This would
  require much less network and CPU but it could make it extremely unlikely
  that any sort of corruption -- even malicious corruption intended to evade
  detection -- would evade detection. This would be an instance of a
  cryptographic notion called "Proof of Retrievability". Note that to implement
  this requires no change to the server or to the cryptographic data structure
  -- with the current data structure and the current protocol it is up to the
  client which blocks they choose to download, so this would be solely a change
  in client behavior.


Security
========

The design goal for this project is that an attacker may be able to deny
service (i.e. prevent you from recovering a file that was uploaded earlier)
but can accomplish none of the following three attacks:

1) violate confidentiality: the attacker gets to view data to which you have
   not granted them access
2) violate integrity: the attacker convinces you that the wrong data is
   actually the data you were intending to retrieve
3) violate unforgeability: the attacker gets to modify a mutable file or
   directory (either the pathnames or the file contents) to which you have
   not given them write permission

Integrity (the promise that the downloaded data will match the uploaded data)
is provided by the hashes embedded in the capability (for immutable files) or
the digital signature (for mutable files). Confidentiality (the promise that
the data is only readable by people with the capability) is provided by the
encryption key embedded in the capability (for both immutable and mutable
files). Data availability (the hope that data which has been uploaded in the
past will be downloadable in the future) is provided by the grid, which
distributes failures in a way that reduces the correlation between individual
node failure and overall file recovery failure, and by the erasure-coding
technique used to generate shares.

Many of these security properties depend upon the usual cryptographic
assumptions: the resistance of AES and RSA to attack, the resistance of
SHA-256 to collision attacks and pre-image attacks, and upon the proximity of
2^-128 and 2^-256 to zero. A break in AES would allow a confidentiality
violation, a collision break in SHA-256 would allow a consistency violation,
and a break in RSA would allow a mutability violation.

There is no attempt made to provide anonymity, neither of the origin of a
piece of data nor the identity of the subsequent downloaders. In general,
anyone who already knows the contents of a file will be in a strong position
to determine who else is uploading or downloading it. Also, it is quite easy
for a sufficiently large coalition of nodes to correlate the set of nodes who
are all uploading or downloading the same file, even if the attacker does not
know the contents of the file in question.

Also note that the file size and (when convergence is being used) a keyed
hash of the plaintext are not protected. Many people can determine the size
of the file you are accessing, and if they already know the contents of a
given file, they will be able to determine that you are uploading or
downloading the same one.

The capability-based security model is used throughout this project.
Directory operations are expressed in terms of distinct read- and write-
capabilities. Knowing the read-capability of a file is equivalent to the
ability to read the corresponding data. The capability to validate the
correctness of a file is strictly weaker than the read-capability (possession
of read-capability automatically grants you possession of
validate-capability, but not vice versa). These capabilities may be expressly
delegated (irrevocably) by simply transferring the relevant secrets.

The application layer can provide whatever access model is desired, built on
top of this capability access model.


Reliability
===========

File encoding and peer-node selection parameters can be adjusted to achieve
different goals. Each choice results in a number of properties; there are
many tradeoffs.

First, some terms: the erasure-coding algorithm is described as ``k``-out-of-``N``
(for this release, the default values are ``k`` = 3 and ``N`` = 10). Each grid will
have some number of nodes; this number will rise and fall over time as nodes
join, drop out, come back, and leave forever. Files are of various sizes, some
are popular, others are unpopular. Nodes have various capacities, variable
upload/download bandwidths, and network latency. Most of the mathematical
models that look at node failure assume some average (and independent)
probability 'P' of a given node being available: this can be high (servers
tend to be online and available >90% of the time) or low (laptops tend to be
turned on for an hour then disappear for several days). Files are encoded in
segments of a given maximum size, which affects memory usage.

The ratio of ``N``/``k`` is the "expansion factor". Higher expansion factors
improve reliability very quickly (the binomial distribution curve is very sharp),
but consumes much more grid capacity. When P=50%, the absolute value of ``k``
affects the granularity of the binomial curve (1-out-of-2 is much worse than
50-out-of-100), but high values asymptotically approach a constant (i.e.
500-of-1000 is not much better than 50-of-100). When P is high and the
expansion factor is held at a constant, higher values of ``k`` and ``N`` give
much better reliability (for P=99%, 50-out-of-100 is much much better than
5-of-10, roughly 10^50 times better), because there are more shares that can
be lost without losing the file.

Likewise, the total number of nodes in the network affects the same
granularity: having only one node means a single point of failure, no matter
how many copies of the file you make. Independent nodes (with uncorrelated
failures) are necessary to hit the mathematical ideals: if you have 100 nodes
but they are all in the same office building, then a single power failure
will take out all of them at once. Pseudospoofing, also called a "Sybil Attack",
is where a single attacker convinces you that they are actually multiple
servers, so that you think you are using a large number of independent nodes,
but in fact you have a single point of failure (where the attacker turns off
all their machines at once). Large grids, with lots of truly independent nodes,
will enable the use of lower expansion factors to achieve the same reliability,
but will increase overhead because each node needs to know something about
every other, and the rate at which nodes come and go will be higher (requiring
network maintenance traffic). Also, the File Repairer work will increase with
larger grids, although then the job can be distributed out to more nodes.

Higher values of ``N`` increase overhead: more shares means more Merkle hashes
that must be included with the data, and more nodes to contact to retrieve
the shares. Smaller segment sizes reduce memory usage (since each segment
must be held in memory while erasure coding runs) and improves "alacrity"
(since downloading can validate a smaller piece of data faster, delivering it
to the target sooner), but also increase overhead (because more blocks means
more Merkle hashes to validate them).

In general, small private grids should work well, but the participants will
have to decide between storage overhead and reliability. Large stable grids
will be able to reduce the expansion factor down to a bare minimum while
still retaining high reliability, but large unstable grids (where nodes are
coming and going very quickly) may require more repair/verification bandwidth
than actual upload/download traffic.
