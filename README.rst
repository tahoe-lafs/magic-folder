Magic Folder
============

**This repository is provisional.**
It may move to a different organization.

Magic Folder for Tahoe-LAFS is a Free and Open file synchronization system.
It detects local changes to files and uploads those changes to a Tahoe-LAFS grid.
It monitors a Tahoe-LAFS grid and downloads changes to the local filesystem.

|readthedocs|  |circleci|  |codecov|

INSTALLING
==========

via pip
^^^^^^^

Then, to install the most recent release, just run:

* ``pip install magic-folder``

from source
^^^^^^^^^^^
To install from source (either so you can hack on it, or just to run
pre-release code), you should create a virtualenv and install into that:

* ``git clone https://github.com/LeastAuthority/magic-folder.git``
* ``cd magic-folder``
* ``virtualenv --python=python2.7 venv``
* ``venv/bin/pip install --upgrade setuptools``
* ``venv/bin/pip install --editable .``
* ``venv/bin/magic-folder --version``

Usage
=====

Magic-Folder is configured via the ``magic-folder`` command-line tool.
This tool manipulates the ``magic_folders.yaml`` file in a Tahoe-LAFS' node's private area.
See the configuration documentation for full details.

Once Magic-Folder is configured,
functionality is provided by running a long-lived magic-folder side-car for the Tahoe-LAFS node.
Start the side-car process using the ``magic-folder`` command line too::

  magic-folder --node-directory /path/to/tahoe-lafs/node run

As long as this side-car is running,
whatever magic folders are configured will be functional.
The process must be restarted to read configuration changes.

TESTING
=======

To run the unit test suite:

* ``tox``

You can pass arguments to ``trial`` with an environment variable.  For
example, you can run the test suite on multiple cores to speed it up:

* ``MAGIC_FOLDER_TRIAL_ARGS="-j4" tox``

Documentation
=============

Documentation is written as reStructuredText documents and processed
using Sphinx; you will need ``sphinx`` and ``sphinx_rtd_theme``.  To
generate HTML version of Magic Folder documents, do:

* ``cd docs; make html``

Resulting HTML files will be under ``docs/_build/html/``.

LICENCE
=======

Copyright 2006-2018 The Tahoe-LAFS Software Foundation
Copyright 2020-2018 The Magic-Folder Developers

You may use this package under the GNU General Public License, version 2 or,
at your option, any later version. You may use this package under the
Transitive Grace Period Public Licence, version 1.0, or at your option, any
later version. (You may choose to use this package under the terms of either
licence, at your option.) See the file `COPYING.GPL`_ for the terms of the
GNU General Public License, version 2. See the file `COPYING.TGPPL`_ for
the terms of the Transitive Grace Period Public Licence, version 1.0.

See `TGPPL.PDF`_ for why the TGPPL exists, graphically illustrated on three
slides.

.. _OSPackages: https://tahoe-lafs.org/trac/tahoe-lafs/wiki/OSPackages
.. _Mac: docs/OS-X.rst
.. _pip: https://pip.pypa.io/en/stable/installing/
.. _COPYING.GPL: https://github.com/tahoe-lafs/tahoe-lafs/blob/master/COPYING.GPL
.. _COPYING.TGPPL: https://github.com/tahoe-lafs/tahoe-lafs/blob/master/COPYING.TGPPL.rst
.. _TGPPL.PDF: https://tahoe-lafs.org/~zooko/tgppl.pdf

----

.. |readthedocs| image:: http://readthedocs.org/projects/magic-folder/badge/?version=latest
    :alt: documentation status
    :target: http://magic-folder.readthedocs.io/en/latest/?badge=latest

.. |circleci| image:: https://circleci.com/gh/LeastAuthority/magic-folder.svg?style=svg
    :target: https://circleci.com/gh/LeastAuthority/magic-folder

.. |codecov| image:: https://codecov.io/github/leastauthority/magic-folder/coverage.svg?branch=master
    :alt: test coverage percentage
    :target: https://codecov.io/github/leastauthority/magic-folder?branch=master
