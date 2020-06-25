
Tahoe-LAFS Magic Folder Frontend
================================

.. Please view a nicely formatted version of this documentation at
   https://magic-folder.readthedocs.io/en/latest/


Introduction
============

The Magic Folder frontend synchronizes local directories on two or more clients.
It uses a Tahoe-LAFS grid for storage.
Whenever a file is created or changed under the local directory of one of the clients,
the change is propagated to the grid and then to the other clients.

The implementation of the "drop-upload" frontend,
on which Magic Folder is based,
was written as a prototype at the First International Tahoe-LAFS Summit in June 2011.
In 2015, with the support of a grant from the `Open Technology Fund`_,
it was redesigned and extended to support synchronization between clients.
It currently works on all major platforms.

Magic Folder is not currently in as mature a state as the other Tahoe-LAFS frontends (web, CLI, SFTP and FTP).
This means that you probably should not rely on all changes to files in the local directory to result in successful uploads.
There might be (and have been) incompatible changes to how the feature is configured.

We are very interested in feedback on how well this feature works for you and suggestions to improve its usability, functionality, and reliability.

.. _`Open Technology Fund`: https://www.opentech.fund/


Contents
--------

.. toctree::
   :maxdepth: 2

   magic-folder
   configuration
   invites
   backdoors
   magic-folder-howto
   proposed/index

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
