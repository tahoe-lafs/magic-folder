Magic-Folder Release Process
============================

There is no schedule for magic-folders releases.
We endeavour to keep ``main`` as always releasable.


Versioning Scheme
-----------------

We use a kind of Calendar Versioning (`https://calver.org/`_):
`YY.MM.NN` where these values are:

* `YY`: the last two digits of the current year;
* `MM`: the two-digit month;
* `NN`: a number that starts at 0 and increases for every release in a given month.


Updating the Version
--------------------

The version is stored as signed Git tags.
`setuptools_scm` handles turning the Git tag into a Python version.


Making a Release
================

The exact process for creating a release is in the `DEVELOPERS` file.
There are also explicit low-level steps in the top-level `Makefile`.
