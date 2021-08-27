
Magic Folder
============

.. Please view a nicely formatted version of this documentation at
   https://magic-folder.readthedocs.io/en/latest/

Magic Folder is a `Tahoe-LAFS`_ front-end that synchronizes local
directories on two or more clients.  It uses a Tahoe-LAFS grid for
storage.  Whenever a file is created or changed under the local
directory of one of the clients, the change is propagated to the grid
and then to the other clients.

The implementation of the "drop-upload" frontend, on which Magic
Folder is based, was written as a prototype at the First International
Tahoe-LAFS Summit in June 2011.  In 2015, with the support of a grant
from the `Open Technology Fund`_, it was redesigned and extended to
support synchronization between clients.  It should work on all major
platforms.

.. warning::

   At the time of this writing, we are in the process of refactoring
   Magic Folder out of Tahoe-LAFS source tree and re-writing it.
   Because of this state of development, the documentation you read
   here may not be up-to-date, and subject to changes.

Magic Folder is not currently in as mature a state as the other
Tahoe-LAFS frontends (web, CLI, SFTP and FTP).  This means that you
probably should not rely on all changes to files in the local
directory to result in successful uploads.  There might be (and have
been) incompatible changes to how the feature is configured.

We are very interested in feedback on how well this feature works for
you.  We welcome suggestions to improve its usability, functionality,
and reliability.  Please file issues you find with Magic Folder at the
`GitHub project`_, or chat with us on IRC in the channel
``#tahoe-lafs`` on ``irc.freenode.net``.

.. _`Open Technology Fund`: https://www.opentech.fund/
.. _`Tahoe-LAFS`: https://tahoe-lafs.org/
.. _`GitHub project`: https://github.com/LeastAuthority/magic-folder

Contents
--------

.. toctree::
   :maxdepth: 1

   CODE_OF_CONDUCT
   usage
   invites
   limitations
   backdoors
   development
   release-process
   proposed/index
