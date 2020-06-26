.. -*- coding: utf-8 -*-

.. _invites:

How invites work
================

Let us clarify the glossary first:

* A **folder** is an abstract directory that is synchronized between
  clients.  A folder is not the same as the directory corresponding to
  it on any particular client, nor is it the same as a DMD.

* **DMD**, abbrevition for Distributed Mutable Directory, is a
  physical Tahoe-LAFS mutable directory.  Each client has the write
  capability to their own DMD, and read capabilities to all other
  client's DMDs.

* A **collective** is the set of clients subscribed to a given Magic
  Folder.

Invitation Process
------------------

Suppose Alice wants to share files with Bob.  These are the steps
Alice would take.

0. Alice creates a magic-folder with a name, say
   ``/home/alice/Documents/Shared``.  She then proceeds to "invite"
   Bob to the folder.  Only Alice as the creator of the folder can
   invite other people into the folder.

1. Alice would create an unlinked directory and get the write
   cap. This directory would form the "DMD" of the folder. Let us call
   it ``dmd_write_cap``.

2. Alice would derive a read-only cap from the write cap derived in
   step #1. Let us call it ``dmd_read_cap``.

3. Alice creates a "collective" directory (Alice has write caps to it)
   and stores an alias corresponds to that directory in her
   ``private/aliases`` file.  Let us call the write cap to the
   collective a ``collective_write_cap``.  Note that there should only
   be one user who has writecaps to the collective folder, since
   concurrent writes to a mutable directory by multiple users is not
   guaranteed to be consistent.

4. Alice derives a read-only cap (``collective_read_cap``) to the
   collective.

5. Alice stores read-only cap from step #2 in ``<collective>/nick``
   file.

6. To join the collective, Alice sends the string,
   ``collective-read-cap + dmd_write_cap`` to Bob.

7. Bob stores the collective_read_cap as "collective_dircap" and the
   ``dmd_write_cap`` as the ``upload_dircap`` in his in config file,
   ``private/magic_folders.yaml``.

At this point Alice has stored Bob's nick in the collective. (Alice
already stored her own nick during the "create" process when she invited
herself into the collective).
