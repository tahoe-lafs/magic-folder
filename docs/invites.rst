.. -*- coding: utf-8 -*-

This document uses semantic newlines.

.. _invites:

How invites work
================

Audience: fellow developers of Magic-Folder

To begin we outline some definitions and assumptions:

* **mutable directory**, is a Tahoe-LAFS mutable directory.
  Tahoe demands that we co-ordinate multiple writes, effectively meaning that only a single device may hold the write-capability for any given mutable.

* **writecap**, **readcap** refer to Tahoe-LAFS directory capabilities here, either read-write or read-only.

* One client ("the admin") holds a write-capability to a **Collective mutable directory** and thus has the ability to add new devices to that collection.
  All other clients have a read-capability to the Collective so they may discover new devices.
  Each entry in the Collective points to a read-capability for the Personal mutable directory of that participant device.

* Every client has a **Personal mutable directory** that they hold the write-capability to.
  The corresponding read-capability appears in the **Collective**.


Overview
--------

A "Desktop" device has a magic-folder.
Since it created this magic-folder it also has the **write** capability to the Collective; i.e. the human controlling this device is the admin.
Desktop wishes to add Laptop as a member of the folder.
Desktop must send to Laptop: a read-capability to the Collective.
Deskopt must receive from Laptop: a read-capability to a fresh "Personal" directory for Laptop.

(If the Laptop is invited as a read-only participant, there's no Personal readcap to send back).

It may be useful to exchange some other information; the above is a minimum.
Desktop is the admin and thus decides what the Laptop device is called.

We exchange messages between the Desktop and Laptop devices via a magic-wormhole.
The built-in "app-versions" mechanism in magic-wormhole is used for protocol negotiation.
Besides the wormhole setup messages themselves, we exchange several message types.
They are all JSON and the exact format of these is detailed later.

It is **important to note** that only read-capabilities are ever exchanged.
Write-capabilities must only ever exist on a single device.

The flow is fairly straightforward:

* on the Desktop device, a wormhole invite-code is created

* (out-of-band the code is securely communicated to the Laptop device)

* on the Laptop device, the wormhole code is presented to the wormhole server, completing the wormhole and allowing secure communication between the devices.

* upon a successful wormhole, Desktop posts its message and Laptop posts its (after creating his Personal directory and extracting the read-capability).

* Desktop creates the Collective entry and posts a final message. Laptop closes the wormhole.

* (If Desktop failed to do this for some reason, an error message is posted instead).

An illustration of the process:

.. seqdiag:: invite-diagram.seqdiag


Detailed Process
----------------

Command-line examples assume that we have a correctly set-up and currently running magic-folder with configuration directory ``~/.magic-folder`` on both the Desktop and Laptop devices.


Creating the Folder
~~~~~~~~~~~~~~~~~~~

Desktop creates a new magic-folder (called "funny-photos")::

    % magic-folder --config ~/.magic-folder add --name funny-photos --author desktop ~/Documents/funny-photos

We can see that it was created::

    % magic-folder --config ~/.magic-folder list
    funny-photos:
        location: /home/desktop/Documents/funny-photos
       stash-dir: /home/desktop/.magic-folder/funny-photos/stash
          author: Desktop (public_key: ...)
         updates: every 60s
           admin: True

Desktop's magic-folder software will now have:

- a write-capability to the "Collective" for "funny-photos".

    - the "Collective" will contain a single entry (``"desktop"``) pointing to the read-capability of Desktop's "Personal" directory

    - we know we have a write-capability because ``admin: True``

- the write-capability for Desktop's "Personal" directory

Users don't usually need to see or care about the read- or write- capabilities; these are used with our Tahoe-LAFS client to do operations.
However, if you do need them you can pass ``--include-secret-information`` to the ``magic-folder list`` command.


Inviting Laptop
~~~~~~~~~~~~~~~

To invite a new (read-write) participant, Desktop needs to send a piece of information and receive a piece of information.
This is accomplished by communicating to the other device by means of `Magic Wormhole <http://magic-wormhole.io>`_.
Since the pieces of information that we exchange are small, we do not need a "bulk transfer" (aka Transit) connection and can send the data via the Magic Wormhole "mailbox" server alone .. so both devices don't strictly need to be online at the same time.
Note that mailbox allocations "time out" so at least one side has to be connected to the mailbox server within this timeout window.

The piece of information we need to send to Laptop is the read-capablity for the "Collective" directory.
The piece of information we need to get from Laptop is the read-capability for its "Personal" directory (unless it is a read-only participant).

Note that Desktop never shares the write-capability to the Collective (nor to the Personal directory) and Laptop never shares the write-capability for the Personal directory.

.. note::

   In tahoe-lafs 1.14.0 and earlier magic-folder the above features were not present; Desktop could impersonate anyone.
   A passive observer of the invite-code could impersonate the invitee indefinitely.

To start the invitation process, Desktop runs ``magic-folder invite``.
This process will tell the human running Desktop a code that looks like ``5-secret-words`` or similar.
The human using Desktop must securely communicate this code to the human who runs Laptop.

All magic-wormholes include a "versions" message through which applications can include arbitrary JSON information (intended for protocol versions and similar).
We use this mechanism for protocol negotiation, including this as the `app_versions=` part::

    {
        "magic-folder": {
            "supported-messages": [
                "invite-v1",
            ],
        },
    }

This tells the other side that: we support magic-folder operations, and support the `invite-v1` messages.
More message may be added in the future.
Communictions MUST stop if ``"invite-v1"`` is not supported (that is, close the mailbox).

All actual messages include a ``"kind"`` and ``"protocol"`` key.
The ``"protocol"`` MUST refer to one of the ``"supported-messages"`` that the peer supports; in this case it will always be ``"invite-v1"`` for the protocol described in this document.
We use ``"kind"`` to distinguish the sort of message this is (within that protocol).

Once the wormhole is established Desktop's magic-folder client sends a message via the wormhole server as JSON::

    {
        "protocol": "invite-v1",
        "kind": "join-folder",
        "folder-name": "<free-form string>",
        "collective": "<read-capability of the Collective>",
        "participant-name": "<admin-provided name>",
        "mode": "read-write",
    }

The ``"mode"`` may be ``"read-write"`` or ``"read-only"``.

Desktop may start this process with the command-line::

    % magic-folder --config ~/.magic-folder invite --name funny-photos --mode read-write laptop
    Invite code: 5-secret-words
      waiting for laptop to accept...

The CLI command accomplishes this using two HTTP APIs: one to start the invite and one to await its completion.
The CLI will now block until the wormhole is completed.
Exiting the process (e.g. ctrl-c) will not kill the invite, though, as that is running in the daemon.
See the HTTP API below for more details.


Accepting the Invitation
~~~~~~~~~~~~~~~~~~~~~~~~

Once the human running Laptop has received a magic-wormhole code from the human running Desktop (for example, "``5-secret-words``") the ``magic-folder join`` command is used on the Latop device to complete the wormhole.

This means that Laptop's client contacts the magic-wormhole server and uses the code-phrase to complete the SPAKE2 transaction.
At this point, Desktop and Laptop have a shared secret key and a "mailbox" allocated on the server -- that is, a secure communication path.
Desktop will have sent the first message; Laptop retrieves this and creates the "Personal" mutable directory (unless it is read-only).
Laptop sends back a message to Desktop (as with all wormhole messages these will be encrypted by the Wormhole library using the shared secret)::

    {
        "protocol": "invite-v1",
        "kind": "join-folder-accept",
        "personal": "<read-capability of Laptop's Personal directory>",
    }

Laptop will not close the wormhole; that will be done by Desktop.
Note that the ``"personal"`` key MUST be absent for read-only participants.
Laptop may accept the invite with the command-line::

    % magic-folder --config ~/.magic-folder join --author laptop --name hilarious-pics 5-secret-words ~/Documents/desktop-fun-pix

If Laptop wishes to reject the connection, a reject message is sent back::

    {
        "protocol": "invite-v1",
        "kind": "join-folder-reject",
        "reject-reason": "free-form string explaining why"
    }

(There is no HTTP API to reject an invitation currently).


Finalizing the Invite
~~~~~~~~~~~~~~~~~~~~~

Once Desktop receives Laptop's reply message Desktop adds Laptop to the Collective.

Desktop writes a new entry into the "Collective" pointing to Laptop's provided Personal read-capability.
In case Laptop is a read-only particpapnt an empty immutable directory is added instead.
In this case, ``laptop -> <Laptop's Personal readcap>``.

Desktop sends a final acknowledgement message to Laptop::

    {
        "protocol": "invite-v1",
        "kind": "join-folder-ack",
        "success": true,
        "participant-name": "laptop"
    }

If there was a problem adding the participant, and error may be sent instead::

    {
        "protocol": "invite-v1",
        "kind": "join-folder-ack",
        "success": false,
        "error": "friendly message"
    }

After one of the above two messages are sent, the wormhole is closed (by Desktop, the inviter).

This concludes the invitation process.
Any other participants will discover Laptop when they next poll the Collective via the read-capabilitiy they were given.


Exchanged Messages
------------------

Looking at the whole process from the magic-wormhole perspective, this is what happens:

- Desktop: allocates a wormhole code, sends the first invite message ``{"collective": "..."}``
- Desktop human: securely communicates the wormhole code to the Laptop human
- Laptop: uses the wormhole code to complete the SPAKE2 handshake.
- Laptop: retrieves the first invite message.
- Laptop: creates Personal (or not, if read-only)
- Laptop: sends the invite reply ``{"personal": "...", }``
- Desktop: retrieves the invite reply.
- Desktop: writes a new entry in the Collective (pointing at Laptop's Personal read-capability)
- Desktop: sends confirmation message ``{"success": true}``
- Desktop: closes the wormhole.


Invite HTTP API
---------------

All Invite functionality is available via HTTP APIs scoped to a particluar magic-folder.
That is, the root URI is ``/experimental/magic-folder/<magic-folder-name>/``.
We describe endpoints below this.


POST .../invite
~~~~~~~~~~~~~~~

Accepts a JSON body containing keys: ``participant-name``, ``mode``.
The ``participant-name`` should be a free-form string with the name for this participant.
The ``mode`` must be ``read-only`` or ``read-write``.
Once the invite is created and a Wormhole code is successfully allocated a reply is rendered.
The reply is a JSON serialization of the invite::

    {
        "id": "<uuid>",
        "participant-name": "valid author name",
        "consumed": bool,
        "success": bool,
        "wormhole-code": "<valid wormhole code>"
    }


POST .../invite-wait
~~~~~~~~~~~~~~~~~~~~

Accepts a JSON body containing keys: ``id``.
The ``id`` is the UUID of an existing invite.
This endpoint will wait until the invite is consumed and then return code 200 with the serialized JSON of the invite (as above) or a 400 error.


POST .../invite-cancel
~~~~~~~~~~~~~~~~~~~~

Accepts a JSON body containing keys: ``id``.
The ``id`` is the UUID of an existing invite.
This endpoint will cancel the pending invite then return code 200 with empty JSON body (``{}``).
In case the invite cannot be cancelled (e.g. it has already succeed or failed or otherwise consumed the wormhole) an error is produced.


GET .../invites
~~~~~~~~~~~~~~~

List currently pending invites.
This returns a serialized JSON list containing all invites known to this client.
Currently invites are ephemeral but aren't deleted, so this will be all invites that have been created since the last time the daemon started.
Note that ``wormhole-code`` may be ``null`` for consumed invites or extremely-recently created invites that haven't yet allocated a code.


POST .../join
~~~~~~~~~~~~~

This is for the client receiving an invite.
This endpoint will accept an invite and create a new magic-folder joined to it.
Takes a JSON body containing the following keys:

- ``invite-code``: the Wormhole code from the inviter
- ``local-directory``: absolute path of an existing local directory to synchronize files in
- ``author``: arbitrary, valid author name
- ``poll-interval``: seconds between remote update checks
- ``scan-interval``: seconds between local update checks

(The ``name`` for the folder comes from the URI).
When the endpoint returns (code 200, empty JSON), the new folder will be added and its services will be running.
