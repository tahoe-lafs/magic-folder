.. -*- coding: utf-8 -*-

.. _configuration:

Using Magic Folder
==================

Magic-Folder is used in two ways.  To interact with configuration, the
``magic-folder`` command line tool is used.  For details of this, see
the section on :ref:`config-file`.  For additional information see
`Magic Folder CLI design`_.

.. _`Magic Folder CLI design`: ../proposed/magic-folder/user-interface-design

To interact with content, use your normal filesystem-based tools.  The
folder which Magic-Folder synchronizes is a normal folder in the
filesystem.  The platform's filesystem change notification features
are used to detect changes.

We think of participants in the system as "devices". A single human
may control many devices (in case they are synchronizing files between
them, for example). It is possible that a magic-folder setup serves
just a single human or it may serve many. Each human may have one or
many devices. When we talk about "an author" below (equivalent to the
``--author`` CLI option, usally) this means a single device. A single
human may control many "authors".


Prerequisites
-------------

You must have one or more Tahoe-LAFS client nodes configured to be
able to store objects somewhere.  They must be able to reach their
configured storage nodes.  The client nodes must all share the same
storage nodes.  The nodes must be running.


Creating a Magic Folder Daemon Configuration
--------------------------------------------

The "Magic Folder Daemon" is a long-running process that accomplishes
the task of synchronizing all the "magic folders" that are configured
in it.

A Magic Folder Daemon needs to have some configuration to start. There
are two ways to create this: from scratch; or from a "legacy"
magic-folder (when ``magic-folder`` was a sub-command of ``tahoe`` in
version 1.14.0 and earlier).

No matter the method used, there are some pieces of information
required and two decisions to be made. You need to know:

- the ``node-directory`` of the Tahoe-LAFS client this magic folder daemon shall use.

You need to decide:

- The network endpoint the API will listen on. This is how CLI
  commands and front-ends communicate to the magic-folder daemon and
  is expressed as a Twisted "server endpoint string". For example,
  ``tcp:4321:interface=localhost`` to listen locally on port 4321.
- If you wish to specify where to store the configuration. By default
  it will be in an appropriate location for your OS (like
  ``~/.config/magic-folder`` on Debian).

To create a magic folder daemon configuration from scratch:

.. code-block:: console

   $ magic-folder init --config ./foo --listen-endpoint tcp:4321:interface=localhost --node-directory ./tahoe-client

This will store configuration in ``./foo``; listen on ``localhost``
port ``4321`` for API commands; and talk to the Tahoe-LAFS client in
``./tahoe-client`` (which must itself be running).

To create a magic folder daemon configuration by migrating an existing
Tahoe-LAFS-based magic-folder, do:

.. code-block:: console

   $ magic-folder migrate --config ./foo --listen-endpoint tcp:4321:interface=localhost --node-directory ./tahoe-client --author alice

The main difference is the ``--author`` argument. This is required
when creating signing keys for each configured magic-folder that is
migrated over to the new daemon. Note that the Tahoe-LAFS
configuration (``./tahoe-client`` in the example) will be left alone.

From now on, we will assume there is a valid magic folder daemon
configuration in ``./foo``. This is usually provided to all
sub-commands like so: ``magic-folder --config ./foo
<subcommand>``. The default location is used if ``--config`` is not
specified.


Running a Magic Folder process
------------------------------

Our magic folder daemon has a configuration location so now it can be
started. It must be running for most of the other magic-folder
commands to work as they use the API.

.. code-block:: console

   $ magic-folder --config ./foo run

Remember that the Tahoe-LAFS node which the daemon uses to upload and
download items from the Grid must also be running.


Creating Magic Folders
----------------------

A new magic folder is added using the ``magic-folder add``
command.

.. code-block:: console

   $ magic-folder --config ./foo add --author alice-laptop --name example ~/Documents

There are some other options that can be specified. The above will
create a new magic-folder named ``example`` (we could decide
differently with ``--name docs`` for example). Any changes we make
locally will be signed as ``alice-laptop``. Files from other devices
are downloaded into ``~/Documents`` and any files we add or change in
that local directory will be uploaded. Note that deleting a file in
``~/Documents`` will record a new "deleted" version in Tahoe Grid and
not actually remove data.

It is also possible to specify ``--poll-interval`` to control how
often the daemon will check for updates if the default seems wrong.

This device will be the administrator for a magic folder created in
this manner (that is, only this device can invite new participants).

See ``magic-folder create --help`` for specific usage details.


Listing Magic Folders
---------------------

Existing magic folders can be listed using the ``magic-folder list``
command:

.. code-block:: console

   $ magic-folder --config ./foo list
   This client has the following magic-folders:
   example:
       location: /home/alice/Documents
      stash-dir: /home/alice/foo/example/stash
         author: alice-laptop (public_key: KSYPPXN3HTCSEJC56RRYXDEO2TZX5LO743Q3E2M7NA7UP2W3OK2A====)
        updates: every 60s

To get JSON output, pass ``--json``.  You can include sensitive secret
information by passing ``--include-secret-information`` flag. Someone
who obtains this information can impersonate this device and
participate as it in the magic folder (if they also gain access to the
Tahoe-LAFS Grid being used).


Inviting Participant Devices
----------------------------

A new participant device is invited to collaborate on a magic folder
using the ``magic-folder invite`` command. This produces an "invite
code" which is a one-time code. This code should be communicated
securely to the invitee. The code will allow the invitee's device to
establish a connection to this device and exchange details. Thus, the
code can only be used while this device is connected to the
Internet. The code may only be used once, for a single invitee.

.. code-block:: console

   $ magic-folder --config ./foo invite --name example

An invitation code is created using an existing magic folder (``--name
example`` above). The magic-folder identified must have been created on
this device.

Once the invitee runs ``magic-folder join`` (see below) the two
devices will connect and exchange some information; this will complete
the invitation. The "invite" command won't exit until the invitee has
actually completed and will print out some details. If you pass
``--no-wait`` then the command will exit immediately (although the
invite will still be valid).

XXX DECIDE: should the default be to wait, or to not? Developers are
split on this; maybe some UX research or discussion can solve it? No
matter what, the HTTP API will have to be two-part ("start invite ->
X" and "status of invite X" or "wait for invite X")

Invites are valid until the magic-folder daemon stops running or until
the default number of minutes pass (whichever is sooner). See the
``--timeout`` options for the default (or you can pass a different
number of mintues if you prefer).


Joining a Magic Folder
----------------------

A participant device accepts an invitation using the ``magic-folder
join`` command:

.. code-block:: console

   $ magic-folder --config ./foo join $INVITECODE /home/bob/Documents/Shared

The first argument required is an invitation code, as described in
`Inviting Participant Devices`_.  The second argument
required is the path to a local directory.  This is the directory to
which content will be downloaded and from which it will be uploaded.

You must choose a name to identify content from this device with
``--author``. The device which has invited you must also be connected
to the internet for the invite to work: once a connection is
established, the two devices exchange some information and the invite
is complete.

Further options are documented in ``magic-folder join --help``.


Leaving a Magic Folder
----------------------

A participant device can reverse the action of joining a magic folder
using the ``magic-folder leave`` command.

You must supply the name of the magic folder to leave with ``--name``.
Once a device has left a magic folder, further changes to files in the
folder will not be synchronized.  The local synchronized directory
itself is not removed. **All configuration and state for the
magic-folder is destroyed**.

Note that by default you cannot leave a folder that this device has
created as it has the only copy of the write-capability which allows
one to change the list of participants. If you really do want to
``leave`` such a folder you can indicate this desire and override the
error with ``--really-delete-write-capability``.

See ``magic-folder leave --help`` for details.



A quick test
------------

If you want to test that things work as expected using a single
machine, you can create two separate Tahoe-LAFS nodes, and assign
corresponding magic folders with them, like so:

.. code-block:: console

   $ export ALICE_NODE=./grid/alice
   $ export ALICE_FOLDER=./alice-sync-dir
   $ export ALICE_MAGIC=./grid/alice-magic

   $ export BOB_NODE=./grid/bob
   $ export BOB_FOLDER=./bob-sync-dir
   $ export BOB_MAGIC=./grid/bob-magic

   # create magic-folder daemons and run them for alice+bob
   $ mkdir -p $ALICE_FOLDER
   $ mkdir -p $BOB_FOLDER
   $ magic-folder init --node-directory $ALICE_NODE --listen-endpoint tcp:4000:interface=localhost --config $ALICE_MAGIC
   $ magic-folder init --node-directory $BOB_NODE --listen-endpoint tcp:4001:interface=localhost --config $BOB_MAGIC
   $ daemonize magic-folder --config $ALICE_MAGIC run
   $ daemonize magic-folder --config $BOB_MAGIC run

   # alice creates a magic-folder and invites bob
   $ magic-folder --config $ALICE_MAGIC add --name example alice $ALICE_FOLDER
   $ magic-folder --config $ALICE_MAGIC invite --name example bob >invitecode
   $ export INVITECODE=$(cat invitecode)
   $ magic-folder --config $BOB_MAGIC join --name example "$INVITECODE" $BOB_FOLDER

You can now experiment with creating files and directories in
``./alice-magic`` and ``./bob-magic``.  Any changes in one should be
propagated to the other directory.

Note that when a file is deleted, the corresponding file in the other
directory will be renamed to a filename ending in ``.backup``.
Deleting a directory will have no effect.

For other known issues and limitations, see :ref:`Known Issues in
Magic-Folder`.

It is also possible to run the nodes on different machines, to
synchronize between three or more clients, to mix Windows and Linux
clients, and to use multiple servers (as long as the Tahoe-LAFS
encoding parameters are changed).
