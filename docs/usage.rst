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
magic-folder (when ``magic-folder`` was a sub-command of ``tahoe``).

No matter the method used, there are some pieces of information
required and a decision to be made. You need to know:

- the ``node-directory`` of the Tahoe-LAFS client this magic folder daemon shall use.

You need to decide:

- The network endpoint the API will listen on. This is how CLI commands and front-ends communciate to the magic-folder daemon and is expressed as a Twisted "server endpoint string". For example, ``tcp:4321`` to listen globally on port 4321. You may also use Unix Domain sockets on OSes which support that; for example ``unix:/var/run/magic-folder/api-socket``.
- Where to store the configuration (by default, in an appropriate location for your OS like ``~/.config/magic-folder`` on Debian).

To create a magic folder daemon configuration from scratch:

.. code-block:: console

   $ magic-folder init --config ./foo --listen-endpoint tcp:4321:interface=localhost --node-directory ./tahoe-client

This will store configuration in ``~/.config/magic-folder/*`` or
similar listen on ``localhost`` port ``4321`` for API commands and
talk to the Tahoe-LAFS client in ``./tahoe-client`` (which must itself
be running).

To migrate from an existing Tahoe-LAFS-based magic-folder, do:

.. code-block:: console

   $ magic-folder migrate --config ./foo --listen-endpoint tcp:4321:interface=localhost --node-directory ./tahoe-client --author-name alice

The main difference is the ``--author-name`` argument. This is
required when creating signing keys for each configured magic-folder
that is migrated over.

From now on, we will assume there is a valid magic folder daemon
configuration in ``./foo``. This is usually provided to all
sub-commands like so: ``magic-folder --config ./foo <subcommand>``


Running a Magic Folder process
------------------------------

Once a magic folder daemon has a configuration location it can be started. It must be running for most of the other magic-folder commands to work as they use the API.

.. code-block:: console

   $ magic-folder --config ./foo run

Remember that the Tahoe-LAFS node which the daemon uses to upload and
download items from the Grid must also be running.


Creating Magic Folders
----------------------

A new magic folder is added using the ``magic-folder add``
command.

.. code-block:: console

   $ magic-folder --config ./foo add --author-name alice-laptop ~/Documents

There are some other options that can be specified. The above will
create a new magic-folder named ``default`` (we could decide
differently with ``--name docs`` for example). Any changes we make
locally will be signed as ``alice-laptop``. Files from other devices
are downloaded into ``~/Documents`` and any files we add or change in
that local directory will be uploaded.

We can also specify ``--poll-interval`` to control how often the
daemon will check for updates (by default this is 60 seconds).

See ``magic-folder create --help`` for specific usage details.


Listing Magic Folders
---------------------

Existing magic folders can be listed using the ``magic-folder list``
command:

.. code-block:: console

   $ magic-folder --config foo list
   This client has the following magic-folders:
   default:
       location: /home/alice/Documents
         author: alice-laptop (KSYPPXN3HTCSEJC56RRYXDEO2TZX5LO743Q3E2M7NA7UP2W3OK2A====)
      stash-dir: /home/alice/foo/default/stash
     collective: URI:DIR2:o6i3qlwv746umshq4l3ktzzjj4:rip3osvz5aq2bwu5qyijaqp4hb6mtwnnicjdpewkz3d45ew35ksq
       personal: URI:DIR2:r4d3qxahanxsr4ysw466cbwrpe:ba3ze3bsxpkp3npyb6al6okqolqmoipi5sjdmp467mqc5prgxmpa
        updates: every 60s

**BE WARNED** that the information displayed is secret


Inviting Participant Devices
----------------------------

A new participant device is invited to collaborate on a magic folder
using the ``magic-folder invite`` command:


.. code-block:: console

   $ magic-folder --node-directory=alice invite magic: bob

An invitation code is created using an alias for an existing magic
folder (``magic:`` above) and a nickname for the new participant
device (``bob`` above).  The magic folder alias identifies a
previously created magic folder.  The nickname is assigned to the
participant device in the magic folder configuration and grid state.
Note that only the creator of a magic folder can invite new
participant devices.

Joining a Magic Folder
----------------------

A participant device accepts an invitation using the ``magic-folder
join`` command:

.. code-block:: console

   $ magic-folder -d bob join $INVITECODE /home/bob/Documents/Shared

The first argument required is an invitation code, as described in
`Inviting Participant Devices`_ is required.  The second argument
required is the path to a local directory.  This is the directory to
which content will be downloaded and from which it will be uploaded.

Further options are documented in ``magic-folder join --help``.

Leaving a Magic Folder
----------------------

A participant device can reverse the action of joining a magic folder
using the ``magic-folder leave`` command.

The only option which can be supplied (but which has a default) is the
nickname of the magic folder to leave.  Once a device has left a magic
folder, further changes to files in the folder will not be
synchronized.  The local directory is not removed.

See ``magic-folder leave --help`` for details.

.. _config-file:

Magic Folder configuration file
-------------------------------

The commands documented above manipulate ``magic_folders.yaml`` in the
Tahoe-LAFS node's private area.  This is a historical artifact
resulting from the origin of Magic Folder as a part of Tahoe-LAFS
itself. Configuration can be changed by modifying this file directly.

Tahoe-LAFS also has historical configuration for Magic-Folder in the
``tahoe.cfg`` configuration file.  This configuration is deprecated.
In particular, the ``enabled`` boolean in the ``magic_folder`` section
is ignored by Magic-Folder.  It should be set to false to prevent any
Magic-Folder functionality included in Tahoe-LAFS from activating.  To
activate the Magic-Folder configuration for a Tahoe-LAFS node, use
``magic-folder run``.


A quick test
------------

If you want to test that things work as expected using a single
machine, you can create two separate Tahoe-LAFS nodes, and assign
corresponding magic folders with them, like so:

.. code-block:: console

   $ ALICE_NODE=../grid/alice
   $ ALICE_FOLDER=../local/alice

   $ mkdir -p $FOLDER_PATH
   $ magic-folder --node-directory=$ALICE_NODE create magic: alice $FOLDER_PATH
   $ magic-folder --node-directory=$ALICE_NODE invite magic: bob >invitecode
   $ export INVITECODE=$(cat invitecode)

   $ BOB_NODE=../grid/bob
   $ BOB_FOLDER=../local/bob

   $ magic-folder -n $BOB_NODE join "$INVITECODE" $BOB_FOLDER

   $ daemonize magic-folder --node-directory=$ALICE_NODE run
   $ deemonize magic-folder --node-directory=$BOB_NODE run

You can now experiment with creating files and directories in
``../local/alice`` and ``../local/bob``.  Any changes in one should be
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
