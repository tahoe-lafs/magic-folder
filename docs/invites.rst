.. -*- coding: utf-8 -*-

.. _invites:

How invites work
================

Audience: fellow developers of Magic-Folder

The begin we outline some definitions and assumptions:

* **DMD**, abbrevition for Distributed Mutable Directory, is an actual Tahoe-LAFS mutable directory.
  Tahoe demands that we co-ordinate multiple writes, effectively meaning that only a single device may hold the write-capability for any given DMD.

* One client ("the admin") holds a write-capability to a **Collective DMD** and thus has the ability to add new devices to that collection.
  All other clients have a read-capability to the Collective so they may discover new devices.
  Each entry in the Collective DMD points to read-capability for the DMD of that participant device.

* The **collective** is the set of clients subscribed to a given Magic Folder (and we refer to the "Collective DMD" or just "Collective" as the Tahoe mutable containing the canonical list of participants).


Overview
--------

Alice has a magic-folder.
She created this magic-folder, so she also has the **write** capability to the Collective DMD. That is, she is the admin.
Alice wishes to add Bob as a member of the collective.
She must send to Bob: a read-capability to the Collective DMD.
She must receive from Bob: a read-capability to a fresh "Personal DMD" for Bob.

It may be useful to exchange some other information; the above is a minimum.


Detailed Process
----------------

Command-line examples assume that we have a correctly set-up and currently running magic-folder with configuration directory ``~/.magic-folder`` on both Alice and Bob's computers.


Creating the Folder
~~~~~~~~~~~~~~~~~~~

Alice creates a new magic-folder (called "funny-photos")::

    % magic-folder --config ~/.magic-folder add --name funny-photos --author alice ~/Documents/funny-photos

We can see that it was created::

    % magic-folder --config ~/.magic-folder list
    funny-photos:
        location: /home/alice/Documents/funny-photos
       stash-dir: /home/alice/.magic-folder/funny-photos/stash
          author: Alice (public_key: ...)
         updates: every 60s
           admin: True

Her magic-folder software will now have:

- a write-capability to the "Collective DMD" for "funny-photos".

    - the "Collective DMD" will contain a single entry "alice" pointing to the read-capability of Alice's "Personal DMD"

    - we know we have a write-capability because ``admin: True``

- the write-capability for Alice's "Personal DMD"

Users don't usually need to see or care about the read- or write- capabilities; these are used with our Tahoe-LAFS client to do operations.
However, if you do need them you can pass ``--include-secret-information`` to the ``magic-folder list`` command.


Inviting Bob
~~~~~~~~~~~~

To invite a new participant, Alice needs to send a piece of information and receive a piece of information.
This is accomplished by connecting to the other device by means of `Magic Wormhole <http://magic-wormhole.io>`_.
Since the pieces of information that we exchange are small, we do not need a "bulk transfer" connection and can send the data via the Magic Wormhole "mailbox" server .. so both deviecs don't strictly need to be online at the same time.
Note that mailbox allocations "time out" so at least one side has to contact the mailbox server within this timeout window.

The piece of information we need to send to Bob is the read-capablity for the "Collective DMD".
The piece of information we need to get from Bob is the read-capability for his "Personal DMD".

Note that Alice never shares her write-capability to the Collective DMD (nor to her own Personal DMD) and Bob never shares his write-capability for his Personal DMD.

.. note::

   In tahoe-lafs 1.14.0 and earlier magic-folder the above features were not present; Alice could impersonate anyone.
   A passive observer of the invite-code could impersonate the invitee indefinitely.

To start the invitation process, Alice runs ``magic-folder invite``.
This process will tell Alice a code that looks like ``5-secret-words`` or similar.
She must securely communicate this code to the invitee, Bob.
Alice's magic-folder client sends a message via the wormhole server as JSON::

    {
        "magic-folder-invite-version": 1,
        "collective-dmd": "<read-capability of the Collective DMD>",
        "suggested-petname": "bob"
    }

The "``suggested-petname``" is optional (and may be ignored by Bob).
Alice may start this process with the command-line::

    % magic-folder --config ~/.magic-folder invite --name funny-photos bob
    Invite code: 5-secret-words
      waiting for bob to accept...

The CLI command accomplishes this using two HTTP APIs: one to start the invite and one to await its completion.
The CLI will now block until the wormhole is completed.
Exiting the process will not kill the invite, though, as that is running in the daemon.
See the HTTP API below for more details.


Accepting the Invitation
~~~~~~~~~~~~~~~~~~~~~~~~

Once Bob has received a magic-wormhole code from Alice (for example, "``5-secret-words``") he will use the ``magic-folder join`` command to complete the wormhole.

This means that Bob's client contacts the magic-wormhole server and uses the code-phrase to complete the SPAKE2 transaction.
At this point, Alice and Bob have a shared secret key and a "mailbox" allocated on the server.
Alice will have sent the first message; Bob retrieves this and creates a mutable directory for his "Personal DMD".
Bob creates a message to send back to Alice encrypted using the shared secret (as JSON)::

    {
        "magic-folder-invite-version": 1,
        "personal-dmd": "<read-capability of Bob's Personal DMD>",
        "preferred-petname": "bobby"
    }

This concludes the invitation process.
Bob will not close the wormhole; that will be done by Alice.
Bob may accept the invite with the command-line::

    % magic-folder --config ~/.magic-folder join --author bobby --name hilarious-pics 5-secret-words ~/Documents/alice-fun-pix

If Bob wishes to reject the connection, a reject message is sent back (not implemented)::

    {
        "magic-folder-invite-version": 1,
        "reject-reason": "free-form string explaining why"
    }

(There is no HTTP API to reject an invitation currently).


Finalizing the Invite
~~~~~~~~~~~~~~~~~~~~~

Once Alice receives Bob's reply message the wormhole is closed (by Alice, not Bob).
Alice adds Bob to the Collective DMD.
Bob MUST send a "``preferred-petname``" and Alice MUST use this name (provided it is unique).

Alice writes a new entry into the "Collective DMD" pointing to Bob's provided Personal DMD read-capability.
In this case, ``bobby -> <Bob's Personal DMD>``.

This concludes the invitation process.
All other participants will discover Bob when they next poll the Collective DMD via the read-capabilitiy they were given.
Bob can learn that his invite is officially concluded in the same way.


Exchanged Messages
------------------

Looking at the whole process from the magic-wormhole perspective, this is what happens:

- Alice: allocates a wormhole code, sends the first invite message ``{"collective-dmd": "..."}``
- Alice (the human): securely communicates the wormhole code to Bob (the human)
- Bob: uses the wormhole code to complete the SPAKE2 handshake.
- Bob: retrieves the first invite message.
- Bob: creates Personal DMD
- Bob: sends the invite reply ``{"personal-dmd": "...", "preferred-petname": "bobby"}``
- Alice: retrieves the invite reply.
- Alice: closes the wormhole.
- Alice: writes a new entry in the Collective DMD (pointing at Bob's Personal DMD read-capability)


Invite HTTP API
---------------

All Invite functionality is available via HTTP APIs scoped to a particluar magic-folder.
That is, the root URI is `/v1/magic-folder/<magic-folder-name>/`.
We describe endpoints below this.


POST .../invite
~~~~~~~~~~~~~~~

Accepts a JSON body containing keys: `suggested-petname`.
This should be a free-form string suggesting a name for this participant.
Once the invite is created and a Wormhole code is successfully allocated a reply is rendered.
The reply is a JSON serialization of the invite::

    {
        "id": "<uuid>",
        "petname": "valid author name",
        "consumed": bool,
        "success": bool,
        "wormhole-code": "<valid wormhole code>"
    }


POST .../invite-wait
~~~~~~~~~~~~~~~~~~~~

Accepts a JSON body containing keys: `id`.
The `id` is the UUID of an existing invite.
This endpoint will wait until the invite is consumed and then return code 200 with the serialized JSON of the invite (as above) or an error.


GET .../invites
~~~~~~~~~~~~~~~

List currently pending invites.
This returns a serialized JSON list containing all invites known to this client.
Currently invites are ephemeral but aren't deleted, so this will be all invites that have been created since the last time the daemon started.
Note that `wormhole-code` may be `null` for consumed invites or extremely-recently created invites that haven't yet allocated a code.


POST .../accept-invite
~~~~~~~~~~~~~~~~~~~~~~

This is for the client receiving an invite.
This endpoint will accept an invite and create a new magic-folder joined to it.
Takes a JSON body containing the following keys:

- `name`: arbitrary, valid magic folder name
- `invite-code`: the Wormhole code from the inviter
- `local-directory`: absolute path of an existing local directory to synchronize files in
- `author`: arbitrary, valid author name
- `poll-interval`: seconds between remote update checks
- `scan-interval`: seconds between local update checks

When the endpoint returns (code 200, empty JSON), the new folder will be added and its services will be running.
