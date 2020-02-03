﻿.. -*- coding: utf-8-with-signature -*-

===============
Download status
===============


Introduction
============

The WUI will display the "status" of uploads and downloads.

The Welcome Page has a link entitled "Recent Uploads and Downloads"
which goes to this URL:

http://$GATEWAY/status

Each entry in the list of recent operations has a "status" link which
will take you to a page describing that operation.

For immutable downloads, the page has a lot of information, and this
document is to explain what it all means. It was written by Brian
Warner, who wrote the v1.8.0 downloader code and the code which
generates this status report about the v1.8.0 downloader's
behavior. Brian posted it to the trac:
https://tahoe-lafs.org/trac/tahoe-lafs/ticket/1169#comment:1

Then Zooko lightly edited it while copying it into the docs/
directory.

What's involved in a download?
==============================

Downloads are triggered by read() calls, each with a starting offset (defaults
to 0) and a length (defaults to the whole file). A regular web-API GET request
will result in a whole-file read() call.

Each read() call turns into an ordered sequence of get_segment() calls. A
whole-file read will fetch all segments, in order, but partial reads or
multiple simultaneous reads will result in random-access of segments. Segment
reads always return ciphertext: the layer above that (in read()) is responsible
for decryption.

Before we can satisfy any segment reads, we need to find some shares. ("DYHB"
is an abbreviation for "Do You Have Block", and is the message we send to
storage servers to ask them if they have any shares for us. The name is
historical, from Mojo Nation/Mnet/Mountain View, but nicely distinctive.
Tahoe-LAFS's actual message name is remote_get_buckets().). Responses come
back eventually, or don't.

Once we get enough positive DYHB responses, we have enough shares to start
downloading. We send "block requests" for various pieces of the share.
Responses come back eventually, or don't.

When we get enough block-request responses for a given segment, we can decode
the data and satisfy the segment read.

When the segment read completes, some or all of the segment data is used to
satisfy the read() call (if the read call started or ended in the middle of a
segment, we'll only use part of the data, otherwise we'll use all of it).

Data on the download-status page
================================

DYHB Requests
-------------

This shows every Do-You-Have-Block query sent to storage servers and their
results. Each line shows the following:

* the serverid to which the request was sent
* the time at which the request was sent. Note that all timestamps are
  relative to the start of the first read() call and indicated with a "+" sign
* the time at which the response was received (if ever)
* the share numbers that the server has, if any
* the elapsed time taken by the request

Also, each line is colored according to the serverid. This color is also used
in the "Requests" section below.

Read Events
-----------

This shows all the FileNode read() calls and their overall results. Each line
shows:

* the range of the file that was requested (as [OFFSET:+LENGTH]). A whole-file
  GET will start at 0 and read the entire file.
* the time at which the read() was made
* the time at which the request finished, either because the last byte of data
  was returned to the read() caller, or because they cancelled the read by
  calling stopProducing (i.e. closing the HTTP connection)
* the number of bytes returned to the caller so far
* the time spent on the read, so far
* the total time spent in AES decryption
* total time spend paused by the client (pauseProducing), generally because the
  HTTP connection filled up, which most streaming media players will do to
  limit how much data they have to buffer
* effective speed of the read(), not including paused time

Segment Events
--------------

This shows each get_segment() call and its resolution. This table is not well
organized, and my post-1.8.0 work will clean it up a lot. In its present form,
it records "request" and "delivery" events separately, indicated by the "type"
column.

Each request shows the segment number being requested and the time at which the
get_segment() call was made.

Each delivery shows:

* segment number
* range of file data (as [OFFSET:+SIZE]) delivered
* elapsed time spent doing ZFEC decoding
* overall elapsed time fetching the segment
* effective speed of the segment fetch

Requests
--------

This shows every block-request sent to the storage servers. Each line shows:

* the server to which the request was sent
* which share number it is referencing
* the portion of the share data being requested (as [OFFSET:+SIZE])
* the time the request was sent
* the time the response was received (if ever)
* the amount of data that was received (which might be less than SIZE if we
  tried to read off the end of the share)
* the elapsed time for the request (RTT=Round-Trip-Time)

Also note that each Request line is colored according to the serverid it was
sent to. And all timestamps are shown relative to the start of the first
read() call: for example the first DYHB message was sent at +0.001393s about
1.4 milliseconds after the read() call started everything off.
