.. -*- coding: utf-8 -*-

.. _configuration:

Configuring Magic-Folder
========================

Magic-Folder configuration lives in a Tahoe-LAFS node's private area.
This is a historical artifact resulting from the origin of Magic-Folder as a part of Tahoe-LAFS itself.

Creating Magic Folders
----------------------

A new magic folder is created using the ``magic-folder create`` command.
A magic folder is created with an alias, a nickname, and a local directory.
The root capability for the new magic folder is assigned the given nickname.
If the nickname and local directory are given,
the client also invites itself to join the folder.
See `Joining a Magic Folder`_ for more details about this.
See ``magic-folder create --help`` for specific usage details.

Inviting Participant Devices
----------------------------

A new participant device is invited to collaborate on a magic folder using the ``magic-folder invite`` command.
An invitation is created using an alias for an existing magic folder and a nickname for the new participant device.
The magic folder alias identifies a previously created magic folder.
The nickname is assigned to the participant device in the magic folder configuration and grid state.
Note that only the creator of a magic folder can invite new participant devices.

Joining a Magic Folder
----------------------

A participant device accepts an invitation using the ``magic-folder join`` command.
The first argument required is an invitation code,
as described in `Inviting Participant Devices`_ is required.
The second argument required is the path to a local directory.
This is the directory to which content will be downloaded and from which it will be uploaded.
Further options are documented in ``magic-folder join --help``.

Leaving a Magic Folder
----------------------

A participant device can reverse the action of joining a magic folder using the ``magic-folder leave`` command.
The only option which can be supplied (but which has a default) is the nickname of the magic folder to leave.
Once a device has left a magic folder,
further changes to files in the folder will not be synchronized.
The local directory is not removed.
See ``magic-folder leave --help`` for details.


Magic Folder configuration file
-------------------------------

The commands documented above manipulate ``magic_folders.yaml`` in the
Tahoe-LAFS node's private area.  Configuration can be changed by
modifying this file directly.

Tahoe-LAFS also has historical configuration for Magic-Folder in the
``tahoe.cfg`` configuration file.  This configuration is deprecated.
In particular, the ``enabled`` boolean in the ``magic_folder`` section
is ignored by Magic-Folder.  It should be set to false to prevent any
Magic-Folder functionality included in Tahoe-LAFS from activating.  To
activate the Magic-Folder configuration for a Tahoe-LAFS node, use
``magic-folder run``.
