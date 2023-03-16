
Magic Folder
============

.. Please view a nicely formatted version of this documentation at
   https://magic-folder.readthedocs.io/en/latest/

Magic Folder is a `Tahoe-LAFS`_ front-end that synchronizes local
directories on two or more clients.  It uses a Tahoe-LAFS grid for
storage.  A daemon scans for local changes and polls the Tahoe-LAFS
grid for remote changes. Whenever a file is created or changed under
the local directory of one of the clients, the change is propagated to
the grid and then to the other clients.

Users of this project must be comfortable with the command-line. Users
wanting a GUI should try `Gridsync`_ (which uses Magic Folder behind
the scenes).

.. warning::

   Releases are ongoing but we do not yet commit to a particular
   stable API. That said, many parts are in place and used by projects
   such as `Gridsync`_ (and we do not expect substantial changes).

   We encourage adventorous users and fellow developers to
   experiment. Integration is via an authenticated localhost :ref:`http-api`.

Other participants to a synchronized folder are invited using `Magic Wormhole`_.
This alows the use of easy-to-transcribe (yet still secure) codes to
facilitate end-to-end encrypted communication between the two
devices. (Note this means contacting a `Mailbox Server`_ run by a
third party).


Feedback Please
===============

We are very interested in feedback on how well this feature works for
you.  We welcome suggestions to improve its usability, functionality,
and reliability.  Please file issues you find with Magic Folder at the
`GitHub project`_, or chat with us on IRC in the channel
``#tahoe-lafs`` on ``irc.freenode.net``.


History of Magic Folder
=======================

The implementation of the "drop-upload" frontend, on which Magic
Folder is based, was written as a prototype at the First International
Tahoe-LAFS Summit in June 2011.  In 2015, with the support of a grant
from the `Open Technology Fund`_, it was redesigned and extended to
support synchronization between clients.  It should work on all major
platforms.

Subsequent to that, Magic Folder was made into a stand-alone project
(and daemon) with substantial changes including an improved datamodel
-- allowing support for robust conflict detection, among other
features. Some of this work was supported by an `Open Technology
Fund`_ grant.


.. _`Open Technology Fund`: https://www.opentech.fund/
.. _`Tahoe-LAFS`: https://tahoe-lafs.org/
.. _`GitHub project`: https://github.com/LeastAuthority/magic-folder
.. _`Gridsync`: https://github.com/gridsync/gridsync/
.. _`Magic Wormhole`: https://github.com/magic-wormhole/magic-wormhole
.. _`Mailbox Server`: https://github.com/magic-wormhole/magic-wormhole-mailbox-server


Contents
--------

.. toctree::
   :maxdepth: 1

   CODE_OF_CONDUCT
   usage
   invites
   limitations
   releases
   backdoors
   development
   interface
   config
   snapshots
   datamodel
   downloader
   release-process
   leif-design
   proposed/index
