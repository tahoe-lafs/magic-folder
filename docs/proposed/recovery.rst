.. -*- coding: utf-8 -*-

.. _snapshots:

Audience
========

This document is aimed at programmers working on integrating with magic-folder.


Motivation
==========

It is useful to be able to back up enough information in order to recover a magic-folder on a new device.
This document describes both the process of gathering the informatino to back up and using it to re-create a similar magic-folder.


Overview
========

A magic-folder consists of a Collective DMD containing sub-directories pointing at the read-capabilities of one or more participant's Personal DMDs.
If this magic-folder instance is the "admin" instance, it will contain a write-capability for the Collective.
It will also contain a write-capablity corresponding to the Personal DMD.

The rough process of "recovery" is:

- create a new, fresh Magic Folder on the new instance
- add the "recovery" instance as a participant in this new Magic Folder

The new instance's local magic-path will fill up with all the files in the old instance as the downloader gets to them.
In a usual case of "recovery" from e.g. a lost or broken device, this participant would not produce new Snapshots.
However, if it does start producing new Snapshots the Magic Folder will continue to synchronize and detect conflicts as normal.

**Note:** this document is only concerned with single-participant Magic Folders for now and does not cover recovery of other participants.


Gathering the Information
-------------------------

In order to recover a Magic Folder on a new device you will need some information about it:

- the Collective DMD (either write-capability or read-capability)
- the Personal DMD (usually a write-capability but you could choose to only save the read-capability corresponding to it)

This can be gathered for all folders at once with a ``magic-folder`` command::

    $ magic-folder list --json --include-secret-information

Running that command will produce a JSON ``object`` containing one key for each Magic Folder.
These will point to a ``object`` containing all the information about this Magic Folder.
The relevant keys here are:
- ``"upload-dircap"``: our Personal DMD write-capability
- ``"collective-dircap"``: a write-capability or read-capability to the Collective DMD (not used in this revision of this document but it is useful to save)

You could choose to keep _all_ of the JSON from the ``magic-folder list`` incantation.

**Note:** these capability-strings are **secret information** and must be stored securely.
Anyone who passively observes them can access all files in the Magic Folder forever and may impersonate this device (if they have access to the same Tahoe grid).


Restoring a Magic Folder
------------------------

Let us assume you have saved the JSON for a single Magic Folder (as returned from ``magic-folder list`` incantaion as above) which looks like::

    "default": {
        "name": "default",
        "author": {
            "name": "laptop",
            "signing_key": "UF5FBIDFVAXOX3XEZVPTEEGKZBEMIRGKO2T5BFWXNGQNASGEC2BA====",
            "verify_key": "HRBIWJZMREI7I3MYIEUZ4MOWMCGH5SMFHCDW3VAT3EWHUPXJJ76Q===="
        },
        "upload_dircap": "URI:DIR2:6izqnla37sdnefnukjs7dbta64:cefhacik6stdiyq577l5zy4jpvnmw7bepriqxydad6voc2fiqt2a",
        "poll_interval": 120,
        "is_admin": true,
        "collective_dircap": "URI:DIR2:oxpqd6wlmxkjona3elv6rdxqnm:cqdkh6vlbwbkgtqi7wwiwojudbeqaitmrd2435p3vh2ez2kazb4q",
        "stash_path": "/home/meejah/.config/magic-folder/laptop/default/stash",
        "magic_path": "/home/meejah/cat-photos"
    }

Further we assume that your new device or magic-folders instance is in ``~/magicfolder2`` and that you've correctly set this up and have the daemon running.

First, we create a new Magic Folder into which we'll recover::

    $ magic-folder --config ~/magicfolder2 add --author desktop --name recovered_cats --poll-interval 120 ~/favourite-kitties
    Created magic-folder named 'recovered_cats'

Here we only describe the case where you are the admin and there is only a single participant in the original Magic Folder (other cases are to-be-designed still).

Use the ``magic-folder-api`` command to add a new participant to the just-created Magic Folder:

    $ magic-folder-api --config ~/magicfolder2 add-participant --author-name laptop --folder recovered_cats --personal-dmd URI:DIR2-RO:xst3xp2fgqxsrq6vvx4dsgl5zy:cefhacik6stdiyq577l5zy4jpvnmw7bepriqxydad6voc2fiqt2a
    {}

We use the same name ``"laptop"`` here, but you could chose something different (as long as it's not the name of our local participant, which is ``"desktop"`` in this example).
One way to transform the write-capability of the Personal DMD into a read-capability is to use ``magic_folder.util.capabilities.to_readonly_capability`` (it is an error to pass a write-capability to the ``add-participant`` API).
