Magic Folder
============

**This repository is provisional.**
It may move to a different organization.
It's history will almost certainly be rewritten.

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

* ``git clone https://github.com/leastauthority/magic-folder.git``
* ``cd magic-folder``
* ``virtualenv --python=python2.7 venv``
* ``venv/bin/pip install --upgrade setuptools``
* ``venv/bin/pip install --editable .``
* ``venv/bin/magic_folder --version``

To run the unit test suite:

* ``tox``

You can pass arguments to ``trial`` with an environment variable.  For
example, you can run the test suite on multiple cores to speed it up:

* ``MAGIC_FOLDER_TRIAL_ARGS="-j4" tox``

LICENCE
=======

Copyright 2006-2018 The Tahoe-LAFS Software Foundation

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

.. |travis| image:: https://travis-ci.org/leastauthority/magic-folder.png?branch=master
    :alt: build status
    :target: https://travis-ci.org/leastauthority/magic-folder

.. |circleci| image:: https://circleci.com/gh/LeastAuthority/magic-folder.svg?style=svg
    :target: https://circleci.com/gh/LeastAuthority/magic-folder

.. |codecov| image:: https://codecov.io/github/leastauthority/magic-folder/coverage.svg?branch=master
    :alt: test coverage percentage
    :target: https://codecov.io/github/leastauthority/magic-folder?branch=master
