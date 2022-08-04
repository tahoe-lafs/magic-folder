Magic Folder
============

**This repository is provisional.**
It may move to a different organization.

Magic Folder for Tahoe-LAFS is a Free and Open file synchronization system.
It detects local changes to files and uploads those changes to a Tahoe-LAFS grid.
It monitors a Tahoe-LAFS grid and downloads changes to the local filesystem.

|readthedocs|  |gha_linux|  |gha_macos|  |gha_windows|  |codecov|

Installing
==========

for integrators
^^^^^^^^^^^^^^^

When packaging magic-folder, please install using our pinned requirements,
which are tested in CI. This should be done in a virtualenv, or other
isolated python environment, so as to not interfere with system or user
python packages.

    git clone https://github.com/LeastAuthority/magic-folder
    cd magic-folder
    pip install --require-hashes -r requirements/base.txt
    pip install --no-deps .


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

Magic-Folder configuration is kept in a directory.
The default place for this directory is platform-dependant; on Linux it will be in ``~/.config/magic-folder``.
Inside this directory is a database for global configuration and sub-directories to track state and temporary space for each actual magic-folder including a configuration database.
All databases are SQLite.

A running Magic-Folder needs to have access to a Tahoe-LAFS client that it may use to perform operations in the Tahoe-LAFS Grid.
This is referenced by the "node directory" of the Tahoe-LAFS client although actual operations are performed via the Tahoe-LAFS WebUI.

There are two ways to create a new Magic Folder instance (that is, the configuration required).
Create a fresh one with ``magic-folder create`` or migrate from a Tahoe-LAFS 1.14.0 or earlier instance with ``magic-folder migrate``.

Once a Magic-Folder is configured, functionality is provided by running a long-lived magic-folder daemon.
This process is run using the ``magic-folder`` command line tool::

  magic-folder --config <path to Magic Foler directory> run

As long as this process is running, whatever magic folders are configured will be functional.
The process must be restarted to read configuration changes.
All other interactions are via the HTTP API which listens on a local endpoint according to the configuration.
Other ``magic-folder`` subcommands are typically just thin CLI wrappers around a particular HTTP endpoint.



Testing
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

License
=======

Copyright 2006-2018 The Tahoe-LAFS Software Foundation
Copyright 2020-2021 The Magic-Folder Developers

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

.. |gha_linux| image:: https://github.com/leastauthority/magic-folder/actions/workflows/linux.yml/badge.svg
    :target: https://github.com/LeastAuthority/magic-folder/actions/workflows/linux.yml

.. |gha_macos| image:: https://github.com/leastauthority/magic-folder/actions/workflows/macos.yaml/badge.svg
    :target: https://github.com/LeastAuthority/magic-folder/actions/workflows/macos.yaml

.. |gha_windows| image:: https://github.com/leastauthority/magic-folder/actions/workflows/windows.yml/badge.svg
    :target: https://github.com/LeastAuthority/magic-folder/actions/workflows/windows.yml

.. |codecov| image:: https://codecov.io/github/leastauthority/magic-folder/coverage.svg?branch=main
    :alt: test coverage percentage
    :target: https://codecov.io/github/leastauthority/magic-folder?branch=main
