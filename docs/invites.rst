.. -*- coding: utf-8 -*-

.. _invites:

How invites work
================

Audience: fellow developers of Magic-Folder

Let us clarify the glossary first:

* A **folder** is an abstract directory that is synchronized between
  clients.  A folder is not the same as the directory corresponding to
  it on any particular client, nor is it the same as a DMD.

* **DMD**, abbrevition for Distributed Mutable Directory, is an actual
  Tahoe-LAFS mutable directory.  Each client has the write capability
  to their so-called "Personal DMD" and read capabilities to all other
  client's DMDs.

* One client holds a write-capability to a **Collective DMD** and thus
  has the ability to add new devices to that collection. All other
  clients have a read-capability so they may discover new
  devices. Each entry in the Collective DMD is a read-capability for
  the DMD of that device.

* A **collective** is the set of clients subscribed to a given Magic
  Folder.


Overview
--------

Alice has a magic-folder.
She created this magic-folder, so she also has the **write** capability to the Collective DMD. That is, she is the admin.
Alice wishes to add Bob as a member of the collective.
She must send to Bob: a read-capability to the Collective DMD.
She must receive from Bob: a read-capability to a fresh "Personal DMD" for Bob.


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

    - the "Collective DMD" will contain a single entry: "alice" with a
      pointer to the read-capability of Alice's "Personal DMD"

    - we know we have a write-capability because ``admin: True``

- the write-capability for Alice's "Personal DMD"

Users don't usually need to see or care about the read- or write- capabilities; these are used with our Tahoe-LAFS client to do operations. However, if you do need them you can pass ``--include-secret-information`` to the ``magic-folder list`` command


Inviting Bob
~~~~~~~~~~~~

To invite a new participant, Alice needs to send a piece of information and receive a piece of information.
This is accomplished by connecting to the other device by means of `Magic Wormhole <http://magic-wormhole.io>`_.
Since the pieces of information that we exchange are small, we do not need a "bulk transfer" connection and can send the data via the Magic Wormhole "mailbox" server .. so both deviecs don't strictly need to be online at the same time.
Note that mailbox allocations "time out" so at least one side has to contact the mailbox server within this timeout window.

The piece of information we need to send to Bob is the read-capablity for the "Collective DMD".
The piece of information we need to get from Bob is the read-capability for his "Personal DMD".

Note that Alice never shares her write-capability to the Collective DMD (nor to her own Personal DMD) and Bob never shares his write-capability for his Personal DMD.

NB: in tahoe-lafs 1.14.0 and earlier magic-folder the above features were not present; Alice could impersonate anyone. A passive observer of the invite-code could impersonate the invitee indefinitely.

To start the invitation process, Alice runs ``magic-folder invite``.
This process will tell Alice a code that looks like ``5-secret-words`` or similar.
She must securely communicate this code to the invitee, Bob.
Alice's magic-folder client sends a message via the wormhole server, encrypted to Bob, as JSON::

    {
        "magic-folder-invite-version": 1,
        "collective-dmd": "<read-capability of the Collective DMD>",
        "suggested-petname": "bob"
    }

The "``suggested-petname``" is optional (and may be ignored by Bob).
Alice may start this process with the command-line::

    % magic-folder --config ~/.magic-folder invite --name funny-photos bob
    Invite code: 5-secret-words
    Waiting for invitee (do not exit this process)...


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

The "``preferred-petname``" key is optional. This concludes the invitation process. Bob will not close the wormhole; that will be done by Alice. Bob may accept the invite with the command-line::

    % magic-folder --config ~/.magic-folder join --author bobby --name hilarious-pics 5-secret-words ~/Documents/alice-fun-pix
    Contacting magic-wormhole server (do not exit this process)...

If Bob wishes to reject the connection, a reject message is sent back::

    {
        "magic-folder-invite-version": 1,
        "reject-reason": "free-form string explaining why"
    }


Finalizing the Invite
~~~~~~~~~~~~~~~~~~~~~

Once Alice receives Bob's reply message the wormhole is closed (by Alice, not Bob).
Alice adds Bob to the Collective DMD. If Bob sent a "``preferred-petname``" than Alice SHOULD use this name (provided it is unique). Otherwise she SHOULD use the name suggested during the invite.

Alice writes a new entry into the "Collective DMD" pointing to Bob's provided Personal DMD read-capability. In this case, ``bobby -> <Bob's Personal DMD>``.

This concludes the invitation process. All other participants will discover Bob when they next poll the Collective DMD via the read-capabilitiy they were given. Bob can learn that his invite is officially concluded in the same way.


Exchanged Messages
------------------

Looking at the whole process from the magic-wormhole perspective, this is what happens:

Alice: allocates a wormhole code, sends the first invite message ``{"collective-dmd": "..."}``
Alice: securely communicates the wormhole code to Bob
Bob: uses the wormhole code to complete the SPAKE2 handshake.
Bob: retrieves the first invite message.
Bob: creates Personal DMD
Bob: sends the invite reply ``{"personal-dmd": "...", "preferred-petname": "bobby"}``
Alice: retrieves the invite reply.
Alice: closes the wormhole.
Alice: writes a new entry in the Collective DMD (pointing at Bob's Personal DMD read-capability)
