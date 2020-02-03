﻿.. -*- coding: utf-8-with-signature -*-

=============
Tahoe Logging
=============

1.  `Overview`_
2.  `Realtime Logging`_
3.  `Incidents`_
4.  `Working with flogfiles`_
5.  `Gatherers`_

    1.  `Incident Gatherer`_
    2.  `Log Gatherer`_

6.  `Adding log messages`_
7.  `Log Messages During Unit Tests`_

Overview
========

Tahoe uses the Foolscap logging mechanism (known as the "flog" subsystem) to
record information about what is happening inside the Tahoe node. This is
primarily for use by programmers and grid operators who want to find out what
went wrong.

The Foolscap logging system is documented at
`<https://github.com/warner/foolscap/blob/latest-release/doc/logging.rst>`__.

The Foolscap distribution includes a utility named "``flogtool``" that is
used to get access to many Foolscap logging features. ``flogtool`` should get
installed into the same virtualenv as the ``tahoe`` command.


Realtime Logging
================

When you are working on Tahoe code, and want to see what the node is doing,
the easiest tool to use is "``flogtool tail``". This connects to the Tahoe
node and subscribes to hear about all log events. These events are then
displayed to stdout, and optionally saved to a file.

"``flogtool tail``" connects to the "logport", for which the FURL is stored
in ``BASEDIR/private/logport.furl`` . The following command will connect to
this port and start emitting log information::

  flogtool tail BASEDIR/private/logport.furl

The ``--save-to FILENAME`` option will save all received events to a file,
where then can be examined later with "``flogtool dump``" or "``flogtool
web-viewer``". The ``--catch-up`` option will ask the node to dump all stored
events before subscribing to new ones (without ``--catch-up``, you will only
hear about events that occur after the tool has connected and subscribed).

Incidents
=========

Foolscap keeps a short list of recent events in memory. When something goes
wrong, it writes all the history it has (and everything that gets logged in
the next few seconds) into a file called an "incident". These files go into
``BASEDIR/logs/incidents/`` , in a file named
"``incident-TIMESTAMP-UNIQUE.flog.bz2``". The default definition of
"something goes wrong" is the generation of a log event at the ``log.WEIRD``
level or higher, but other criteria could be implemented.

The typical "incident report" we've seen in a large Tahoe grid is about 40kB
compressed, representing about 1800 recent events.

These "flogfiles" have a similar format to the files saved by "``flogtool
tail --save-to``". They are simply lists of log events, with a small header
to indicate which event triggered the incident.

The "``flogtool dump FLOGFILE``" command will take one of these ``.flog.bz2``
files and print their contents to stdout, one line per event. The raw event
dictionaries can be dumped by using "``flogtool dump --verbose FLOGFILE``".

The "``flogtool web-viewer``" command can be used to examine the flogfile in
a web browser. It runs a small HTTP server and emits the URL on stdout.  This
view provides more structure than the output of "``flogtool dump``": the
parent/child relationships of log events is displayed in a nested format.
"``flogtool web-viewer``" is still fairly immature.

Working with flogfiles
======================

The "``flogtool filter``" command can be used to take a large flogfile
(perhaps one created by the log-gatherer, see below) and copy a subset of
events into a second file. This smaller flogfile may be easier to work with
than the original. The arguments to "``flogtool filter``" specify filtering
criteria: a predicate that each event must match to be copied into the target
file. ``--before`` and ``--after`` are used to exclude events outside a given
window of time. ``--above`` will retain events above a certain severity
level. ``--from`` retains events send by a specific tubid.
``--strip-facility`` removes events that were emitted with a given facility
(like ``foolscap.negotiation`` or ``tahoe.upload``).

Gatherers
=========

In a deployed Tahoe grid, it is useful to get log information automatically
transferred to a central log-gatherer host. This offloads the (admittedly
modest) storage requirements to a different host and provides access to
logfiles from multiple nodes (web-API, storage, or helper) in a single place.

There are two kinds of gatherers: "log gatherer" and "stats gatherer". Each
produces a FURL which needs to be placed in the ``NODEDIR/tahoe.cfg`` file of
each node that is to publish to the gatherer, under the keys
"log_gatherer.furl" and "stats_gatherer.furl" respectively. When the Tahoe
node starts, it will connect to the configured gatherers and offer its
logport: the gatherer will then use the logport to subscribe to hear about
events.

The gatherer will write to files in its working directory, which can then be
examined with tools like "``flogtool dump``" as described above.

Incident Gatherer
-----------------

The "incident gatherer" only collects Incidents: records of the log events
that occurred just before and slightly after some high-level "trigger event"
was recorded. Each incident is classified into a "category": a short string
that summarizes what sort of problem took place. These classification
functions are written after examining a new/unknown incident. The idea is to
recognize when the same problem is happening multiple times.

A collection of classification functions that are useful for Tahoe nodes are
provided in ``misc/incident-gatherer/support_classifiers.py`` . There is
roughly one category for each ``log.WEIRD``-or-higher level event in the
Tahoe source code.

The incident gatherer is created with the "``flogtool
create-incident-gatherer WORKDIR``" command, and started with "``tahoe
start``". The generated "``gatherer.tac``" file should be modified to add
classifier functions.

The incident gatherer writes incident names (which are simply the relative
pathname of the ``incident-\*.flog.bz2`` file) into ``classified/CATEGORY``.
For example, the ``classified/mutable-retrieve-uncoordinated-write-error``
file contains a list of all incidents which were triggered by an
uncoordinated write that was detected during mutable file retrieval (caused
when somebody changed the contents of the mutable file in between the node's
mapupdate step and the retrieve step). The ``classified/unknown`` file
contains a list of all incidents that did not match any of the classification
functions.

At startup, the incident gatherer will automatically reclassify any incident
report which is not mentioned in any of the ``classified/\*`` files. So the
usual workflow is to examine the incidents in ``classified/unknown``, add a
new classification function, delete ``classified/unknown``, then bound the
gatherer with "``tahoe restart WORKDIR``". The incidents which can be
classified with the new functions will be added to their own
``classified/FOO`` lists, and the remaining ones will be put in
``classified/unknown``, where the process can be repeated until all events
are classifiable.

The incident gatherer is still fairly immature: future versions will have a
web interface and an RSS feed, so operations personnel can track problems in
the storage grid.

In our experience, each incident takes about two seconds to transfer from the
node that generated it to the gatherer. The gatherer will automatically catch
up to any incidents which occurred while it is offline.

Log Gatherer
------------

The "Log Gatherer" subscribes to hear about every single event published by
the connected nodes, regardless of severity. This server writes these log
events into a large flogfile that is rotated (closed, compressed, and
replaced with a new one) on a periodic basis. Each flogfile is named
according to the range of time it represents, with names like
"``from-2008-08-26-132256--to-2008-08-26-162256.flog.bz2``". The flogfiles
contain events from many different sources, making it easier to correlate
things that happened on multiple machines (such as comparing a client node
making a request with the storage servers that respond to that request).

Create the Log Gatherer with the "``flogtool create-gatherer WORKDIR``"
command, and start it with "``tahoe start``". Then copy the contents of the
``log_gatherer.furl`` file it creates into the ``BASEDIR/tahoe.cfg`` file
(under the key ``log_gatherer.furl`` of the section ``[node]``) of all nodes
that should be sending it log events. (See :doc:`configuration`)

The "``flogtool filter``" command, described above, is useful to cut down the
potentially large flogfiles into a more focussed form.

Busy nodes, particularly web-API nodes which are performing recursive
deep-size/deep-stats/deep-check operations, can produce a lot of log events.
To avoid overwhelming the node (and using an unbounded amount of memory for
the outbound TCP queue), publishing nodes will start dropping log events when
the outbound queue grows too large. When this occurs, there will be gaps
(non-sequential event numbers) in the log-gatherer's flogfiles.

Adding log messages
===================

When adding new code, the Tahoe developer should add a reasonable number of
new log events. For details, please see the Foolscap logging documentation,
but a few notes are worth stating here:

* use a facility prefix of "``tahoe.``", like "``tahoe.mutable.publish``"

* assign each severe (``log.WEIRD`` or higher) event a unique message
  identifier, as the ``umid=`` argument to the ``log.msg()`` call. The
  ``misc/coding_tools/make_umid`` script may be useful for this purpose.
  This will make it easier to write a classification function for these
  messages.

* use the ``parent=`` argument whenever the event is causally/temporally
  clustered with its parent. For example, a download process that involves
  three sequential hash fetches could announce the send and receipt of those
  hash-fetch messages with a ``parent=`` argument that ties them to the
  overall download process. However, each new web-API download request should
  be unparented.

* use the ``format=`` argument in preference to the ``message=`` argument.
  E.g. use ``log.msg(format="got %(n)d shares, need %(k)d", n=n, k=k)``
  instead of ``log.msg("got %d shares, need %d" % (n,k))``. This will allow
  later tools to analyze the event without needing to scrape/reconstruct the
  structured data out of the formatted string.

* Pass extra information as extra keyword arguments, even if they aren't
  included in the ``format=`` string. This information will be displayed in
  the "``flogtool dump --verbose``" output, as well as being available to
  other tools. The ``umid=`` argument should be passed this way.

* use ``log.err`` for the catch-all ``addErrback`` that gets attached to the
  end of any given Deferred chain. When used in conjunction with
  ``LOGTOTWISTED=1``, ``log.err()`` will tell Twisted about the error-nature
  of the log message, causing Trial to flunk the test (with an "ERROR"
  indication that prints a copy of the Failure, including a traceback).
  Don't use ``log.err`` for events that are ``BAD`` but handled (like hash
  failures: since these are often deliberately provoked by test code, they
  should not cause test failures): use ``log.msg(level=BAD)`` for those
  instead.


Log Messages During Unit Tests
==============================

If a test is failing and you aren't sure why, start by enabling
``FLOGTOTWISTED=1`` like this::

  make test FLOGTOTWISTED=1

With ``FLOGTOTWISTED=1``, sufficiently-important log events will be written
into ``_trial_temp/test.log``, which may give you more ideas about why the
test is failing.

By default, ``_trial_temp/test.log`` will not receive messages below the
``level=OPERATIONAL`` threshold. You can change the threshold via the ``FLOGLEVEL``
variable, e.g.::

  make test FLOGLEVEL=10 FLOGTOTWISTED=1

(The level numbers are listed in src/allmydata/util/log.py.)

To look at the detailed foolscap logging messages, run the tests like this::

  make test FLOGFILE=flog.out.bz2 FLOGLEVEL=1 FLOGTOTWISTED=1

The first environment variable will cause foolscap log events to be written
to ``./flog.out.bz2`` (instead of merely being recorded in the circular
buffers for the use of remote subscribers or incident reports). The second
will cause all log events to be written out, not just the higher-severity
ones. The third will cause twisted log events (like the markers that indicate
when each unit test is starting and stopping) to be copied into the flogfile,
making it easier to correlate log events with unit tests.

Enabling this form of logging appears to roughly double the runtime of the
unit tests. The ``flog.out.bz2`` file is approximately 2MB.

You can then use "``flogtool dump``" or "``flogtool web-viewer``" on the
resulting ``flog.out`` file.

("``flogtool tail``" and the log-gatherer are not useful during unit tests,
since there is no single Tub to which all the log messages are published).

It is possible for setting these environment variables to cause spurious test
failures in tests with race condition bugs. All known instances of this have
been fixed as of Tahoe-LAFS v1.7.1.
