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


Running a Magic Folder process
------------------------------

If you are running a Tahoe-LAFS client off a Tahoe-LAFS node directory
``alice``, like so:

.. code-block:: console

   $ tahoe --node-directory=alice run

you can start the corresponding magic folder process like so:

.. code-block:: console

   $ magic-folder --node-directory=alice run --web-port tcp:6000


Creating Magic Folders
----------------------

A new magic folder is created using the ``magic-folder create``
command:

.. code-block:: console

   $ magic-folder --node-directory=alice create magic: shared-docs /home/alice/Documents/Shared

A magic folder is created with an alias (such as ``magic:`` in the
above command), a nickname (such as ``shared-docs``), and a local
directory (such as ``~/Documents/Shared``).  The root capability for
the new magic folder is assigned the given nickname.

If the nickname and local directory are given, the client also invites
itself to join the folder.  See `Joining a Magic Folder`_ for more
details about this.

See ``magic-folder create --help`` for specific usage details.

Listing Magic Folders
---------------------

Existing magic folders can be listed using the ``magic-folder list``
command:

.. code-block:: console

   $ magic-folder --node-directory=alice list
   This client has the following magic-folders:
     default: /home/alice/Work/Magic
       magic: /home/alice/Documents/Shared


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
