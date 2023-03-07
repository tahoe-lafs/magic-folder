
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

Subsequent to that, Magic Folder was made into a stand-alone project
(and daemon) with substantial changes including an improved datamodel
allowing support for robust conflict detection, among other features.

.. warning::

   Releases are ongoing but we do not yet commit to a particular
   stable API. That said, many parts are in place and used by projects
   such as `Gridsync`_ (and we do not expect substantial changes).

   We encourage adventorous users and fellow developers to
   experiment. Integration is via an authenticated localhost HTTP API.

We are very interested in feedback on how well this feature works for
you.  We welcome suggestions to improve its usability, functionality,
and reliability.  Please file issues you find with Magic Folder at the
`GitHub project`_, or chat with us on IRC in the channel
``#tahoe-lafs`` on ``irc.freenode.net``.

.. _`Open Technology Fund`: https://www.opentech.fund/
.. _`Tahoe-LAFS`: https://tahoe-lafs.org/
.. _`GitHub project`: https://github.com/LeastAuthority/magic-folder
.. _`Gridsync`: https://github.com/gridsync/gridsync/

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
