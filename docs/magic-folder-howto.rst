.. -*- coding: utf-8 -*-

.. _magic-folder-howto:

Magic Folder Set-up Howto
=========================

#.  `This document`_
#.  `Prerequisites`_
#.  `Setting up Magic Folder`_
#.  `Testing`_


This document
-------------

This is preliminary documentation of how to set up Magic Folder using
an existing Tahoe-LAFS client node.  It is aimed at a fairly technical
audience.

For an introduction to Magic Folder and how to configure it more
generally, see :doc:`usage`.

Prerequisites
-------------

You must have one or more Tahoe-LAFS client nodes configured to be
able to store objects somewhere.  They must be able to reach their
configured storage nodes.  The client nodes must all share the same
storage nodes.  The nodes must be running.

Setting up Magic Folder
-----------------------

Run::

  ALICE_NODE=../grid/alice
  ALICE_FOLDER=../local/alice

  mkdir -p $FOLDER_PATH
  magic-folder -d $NODE_PATH create magic: alice $FOLDER_PATH
  magic-folder -d $NODE_PATH invite magic: bob >invitecode
  export INVITECODE=$(cat invitecode)

  BOB_NODE=../grid/bob
  BOB_FOLDER=../local/bob

  magic-folder -d $BOB_NODE join "$INVITECODE" $BOB_FOLDER

  deamonize magic-folder -d $ALICE_NODE run
  daemonize magic-folder -d $BOB_NODE run

Testing
-------

You can now experiment with creating files and directories in
``../local/alice`` and ``/local/bob``.  Any changes should be
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
