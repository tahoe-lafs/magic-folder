﻿.. -*- coding: utf-8-with-signature -*-

=======================
The Tahoe Upload Helper
=======================

1. `Overview`_
2. `Setting Up A Helper`_
3. `Using a Helper`_
4. `Other Helper Modes`_

Overview
========

As described in the "Swarming Download, Trickling Upload" section of
:doc:`architecture`, Tahoe uploads require more bandwidth than downloads: you
must push the redundant shares during upload, but you do not need to retrieve
them during download. With the default 3-of-10 encoding parameters, this
means that an upload will require about 3.3x the traffic as a download of the
same file.

Unfortunately, this "expansion penalty" occurs in the same upstream direction
that most consumer DSL lines are slow anyways. Typical ADSL lines get 8 times
as much download capacity as upload capacity. When the ADSL upstream penalty
is combined with the expansion penalty, the result is uploads that can take
up to 32 times longer than downloads.

The "Helper" is a service that can mitigate the expansion penalty by
arranging for the client node to send data to a central Helper node instead
of sending it directly to the storage servers. It sends ciphertext to the
Helper, so the security properties remain the same as with non-Helper
uploads. The Helper is responsible for applying the erasure encoding
algorithm and placing the resulting shares on the storage servers.

Of course, the helper cannot mitigate the ADSL upstream penalty.

The second benefit of using an upload helper is that clients who lose their
network connections while uploading a file (because of a network flap, or
because they shut down their laptop while an upload was in progress) can
resume their upload rather than needing to start again from scratch. The
helper holds the partially-uploaded ciphertext on disk, and when the client
tries to upload the same file a second time, it discovers that the partial
ciphertext is already present. The client then only needs to upload the
remaining ciphertext. This reduces the "interrupted upload penalty" to a
minimum.

This also serves to reduce the number of active connections between the
client and the outside world: most of their traffic flows over a single TCP
connection to the helper. This can improve TCP fairness, and should allow
other applications that are sharing the same uplink to compete more evenly
for the limited bandwidth.

Setting Up A Helper
===================

Who should consider running a helper?

* Benevolent entities which wish to provide better upload speed for clients
  that have slow uplinks
* Folks which have machines with upload bandwidth to spare.
* Server grid operators who want clients to connect to a small number of
  helpers rather than a large number of storage servers (a "multi-tier"
  architecture)

What sorts of machines are good candidates for running a helper?

* The Helper needs to have good bandwidth to the storage servers. In
  particular, it needs to have at least 3.3x better upload bandwidth than
  the client does, or the client might as well upload directly to the
  storage servers. In a commercial grid, the helper should be in the same
  colo (and preferably in the same rack) as the storage servers.
* The Helper will take on most of the CPU load involved in uploading a file.
  So having a dedicated machine will give better results.
* The Helper buffers ciphertext on disk, so the host will need at least as
  much free disk space as there will be simultaneous uploads. When an upload
  is interrupted, that space will be used for a longer period of time.

To turn a Tahoe-LAFS node into a helper (i.e. to run a helper service in
addition to whatever else that node is doing), edit the tahoe.cfg file in your
node's base directory and set "enabled = true" in the section named
"[helper]".

Then restart the node. This will signal the node to create a Helper service
and listen for incoming requests. Once the node has started, there will be a
file named private/helper.furl which contains the contact information for the
helper: you will need to give this FURL to any clients that wish to use your
helper.

::

  cat $BASEDIR/private/helper.furl | mail -s "helper furl" friend@example.com

You can tell if your node is running a helper by looking at its web status
page. Assuming that you've set up the 'webport' to use port 3456, point your
browser at ``http://localhost:3456/`` . The welcome page will say "Helper: 0
active uploads" or "Not running helper" as appropriate. The
http://localhost:3456/helper_status page will also provide details on what
the helper is currently doing.

The helper will store the ciphertext that is is fetching from clients in
$BASEDIR/helper/CHK_incoming/ . Once all the ciphertext has been fetched, it
will be moved to $BASEDIR/helper/CHK_encoding/ and erasure-coding will
commence. Once the file is fully encoded and the shares are pushed to the
storage servers, the ciphertext file will be deleted.

If a client disconnects while the ciphertext is being fetched, the partial
ciphertext will remain in CHK_incoming/ until they reconnect and finish
sending it. If a client disconnects while the ciphertext is being encoded,
the data will remain in CHK_encoding/ until they reconnect and encoding is
finished. For long-running and busy helpers, it may be a good idea to delete
files in these directories that have not been modified for a week or two.
Future versions of tahoe will try to self-manage these files a bit better.

Using a Helper
==============

Who should consider using a Helper?

* clients with limited upstream bandwidth, such as a consumer ADSL line
* clients who believe that the helper will give them faster uploads than
  they could achieve with a direct upload
* clients who experience problems with TCP connection fairness: if other
  programs or machines in the same home are getting less than their fair
  share of upload bandwidth. If the connection is being shared fairly, then
  a Tahoe upload that is happening at the same time as a single FTP upload
  should get half the bandwidth.
* clients who have been given the helper.furl by someone who is running a
  Helper and is willing to let them use it

To take advantage of somebody else's Helper, take the helper furl that they
give you, and edit your tahoe.cfg file. Enter the helper's furl into the
value of the key "helper.furl" in the "[client]" section of tahoe.cfg, as
described in the "Client Configuration" section of :doc:`configuration`.

Then restart the node. This will signal the client to try and connect to the
helper. Subsequent uploads will use the helper rather than using direct
connections to the storage server.

If the node has been configured to use a helper, that node's HTTP welcome
page (``http://localhost:3456/``) will say "Helper: $HELPERFURL" instead of
"Helper: None". If the helper is actually running and reachable, the bullet
to the left of "Helper" will be green.

The helper is optional. If a helper is connected when an upload begins, the
upload will use the helper. If there is no helper connection present when an
upload begins, that upload will connect directly to the storage servers. The
client will automatically attempt to reconnect to the helper if the
connection is lost, using the same exponential-backoff algorithm as all other
tahoe/foolscap connections.

The upload/download status page (``http://localhost:3456/status``) will announce
the using-helper-or-not state of each upload, in the "Helper?" column.

Other Helper Modes
==================

The Tahoe Helper only currently helps with one kind of operation: uploading
immutable files. There are three other things it might be able to help with
in the future:

* downloading immutable files
* uploading mutable files (such as directories)
* downloading mutable files (like directories)

Since mutable files are currently limited in size, the ADSL upstream penalty
is not so severe for them. There is no ADSL penalty to downloads, but there
may still be benefit to extending the helper interface to assist with them:
fewer connections to the storage servers, and better TCP fairness.

A future version of the Tahoe helper might provide assistance with these
other modes. If it were to help with all four modes, then the clients would
not need direct connections to the storage servers at all: clients would
connect to helpers, and helpers would connect to servers. For a large grid
with tens of thousands of clients, this might make the grid more scalable.
