.. -*- coding: utf-8 -*-

.. _invites:

How invites work
================

Glossarry
'''''''''

Folder: an abstract directory that is synchronized between clients.
(A folder is not the same as the directory corresponding to it on
any particular client, nor is it the same as a DMD.)
DMD: Distributed Mutable Directory
Collective: the set of clients subscribed to a given Magic Folder.

Invitation Process
==================

Suppose Alice wants to invite Bob to a folder "foo". These are the
steps Alice would take.

1. Alice would create an unlinked directory and get the write cap. Let
us call it `dmd_write_cap`.

2. Alice would derive a read-only cap from the write cap derived in
step #1. Let us call it `dmd_read_cap`.

3. Alice creates a "collective" directory (Alice has write caps to it)
and stores an alias corresponds to that directory in her
"private/aliases" file. Let us call the write cap to the collective
as "collective_write_cap".

4. Alice derives a read-only cap to the collective. ("collective_read_cap")

5. The directory created in step #1 is passes as dmd_write_cap to Bob
along with the read-cap of collective.

6. Alice stores read-only cap from step #2 in <collective>/nick file.

7. To join the collective, Alice sends the string, "collective-read-cap +
dmd_write_cap" to Bob.

8. Bob stores the collective_read_cap as "collective_dircap" and the
"dmd_write_cap" as the "upload_dircap" in his in config file
("private/magic_folders.yaml").

At this point Alice has stored the Bob's nick in the collective. (Alice
already stored her own nick during the "create" process when she invited
herself into the collective).

