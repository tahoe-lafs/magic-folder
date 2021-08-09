Magic-Folder Release Process
============================

This document is an initial effort towards providing documentation for a magic-folder release process.
See `https://github.com/LeastAuthority/magic-folder/issues/532`_ for details about improvements.

The release process involves at least the following steps:

* Update the package version number.
  This must be done in at least:

  * the ``setup`` call in ``setup.py``
  * the ``buildPythonPackage`` call in ``nix/default.nix``
  * the ``__version__`` definition in ``src/magic_folder/__init__.py``
