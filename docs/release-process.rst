Magic-Folder Release Process
============================

There is no schedule for magic-folders releases.
We endeavor to keep ``main`` as always releasable.


Versioning Scheme
-----------------

We use a kind of Calendar Versioning (`https://calver.org/`_):
`YY.MM.NN` where these values are:

* `YY`: the last two digits of the current year;
* `MM`: the two-digit month;
* `NN`: a number that starts at 0 and increases for every release in a given month.


API Stability and Compatibility
-------------------------------

The recommended API is the HTTP API; there is a command-line wrapper of this called `magic-folder-api` which _should_ be in sync.
There is no supported Python API.

**Currently we make no stability guarantees.**

One we change the above statement, the version numbers in the protocols will be updated upon any breaking changes.
Any such changes will also be noted in the release notes.

Integrations should:
* run the Python daemon as a "black box"
* not depend on any on-disc files
* use the HTTP API to communicate

The `magic-folder-api` command is intended as a convenience around the HTTP API and _should_ be in sync with that API (if it is not, that is a bug).
Generally, this endeavors to return the same information in the same way as the HTTP API itself (usually JSON).

The `magic-folder` command and sub-commands are mostly intended for "human" use so parsing their output should not be considered stable.
For automated use it is preferable to use the "low-level" `magic-folder-api` or the HTTP API instead.
(Please reach out if your needs are not served by the latter).


Updating the Version
--------------------

The version is stored as signed Git tags.
`setuptools_scm` handles turning the Git tag into a Python version.


Making a Release
================

The exact process for creating a release is in the `DEVELOPERS` file.
There are also explicit low-level steps in the top-level `Makefile`.
