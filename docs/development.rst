.. _Magic-Folder Development:

Magic-Folder Development
========================

Process Overview
----------------

The high-level view of the Magic-Folder development process looks like this:

#. A shortcoming, defect, or new area of functionality is identified relating to the software.
   For example,
   perhaps files with names matching a certain pattern are never uploaded to the grid
   or a developer decides to add a history browsing user experience to the software.
#. `An issue is filed`_ describing the shortcoming, defect, or new area of functionality.
   The issue describes how the software will be different than it is now and why this is desirable.
#. Development work proceeds in a branch (typically created from a recent main branch revision).
   Prior to this development or in parallel with it,
   communication within the team helps identify and clear roadblocks early.
#. When development has progressed sufficiently,
   the branch is `proposed for review`_.
#. One or more other developers `review the changes`_.
#. The previous two steps may repeat a number of times until the changes pass review.
#. Upon passing review,
   the changes are `merged into the main branch`_.


.. _An issue is filed:

Filing Issues
-------------

The project uses `GitHub issues <https://github.com/leastauthority/magic-folder/issues>`_ for issue tracking.

.. _proposed for review:

Proposing Changes
-----------------

To propose a change,
create a `GitHub Pull Request <https://github.com/leastauthority/magic-folder/pulls>`_ against main.

Changes should:

* have a corresponding ticket.
* be in a branch named ``<ticket number>.<some short descriptive text>``.
* have appropriate developer-facing and user-facing documentation.
* have full line and branch coverage provided by the test suite.
* be implemented in a way that is not unnecessarily difficult to understand and maintain.
* satisfy all of the mechanical checks performed by the continuous integration system.

News Fragments
~~~~~~~~~~~~~~

One of the mechanical checks performed by continuous integration against proposed changes is the existence of a "news fragment".
News fragments are assembled at release time by `towncrier <https://pypi.org/project/towncrier/>`_ to contribute to the release announcement.
News fragments are meant to be user-facing and should have a consistent style.
News fragments can use any inline formatting directives from reStructuredText.

magic-folder's news fragment style is adapted from `the style guidelines from the Twisted project`_.
The fragment types accepted are canonically defined by the towncrier configuration file in the project root.

Here are a few guidelines which should help you write good news fragments:

* The entry SHOULD contain a high-level description of the change suitable for end users.
* When the changes touch Python code,
  the grammatical subject of the sentence SHOULD be a Python class/method/function/interface/variable/etc,
  and the verb SHOULD be something that the object does.
  The verb MAY be prefixed with "now".
* When the changes touch user interface,
  the grammatical subject of the sentence SHOULD identify the part of the user interface affected
  and the verb SHOULD be something that the user interface does.
* For bugfix,
  it MAY contain a reference to the version in which the bug was introduced.

Fragment files MUST be placed in ``newsfragments`` and be named ``<ticket number>.<fragment type>``.
You can preview the generated news content using ``tox -e draftnews``.

Backwards Incompatible Changes
``````````````````````````````

The fragment type is ``incompat``.
Here are some examples.

::

   ``magic-folder create`` now requires the ``--path`` option.

::

   Files in a Magic Folder with a name beginning with ``.`` are now uploaded instead of ignored.

Features
````````

The fragment type is ``feature``.
Here are some examples.

::

   The downloader can now detect three-party conflicts.

::

   On Windows, the uploader now notices file changes immediately instead of after a several minute delay.

Bug Fixes
`````````

The fragment type is ``bugfix``.
Here are some examples.

::

   The uploader no longer fails to upload all files with a space in their name.

::

   The downloader now writes all files beneath the Magic Folder directory instead of the user's home directory.

Dependency/Installation Changes
```````````````````````````````

The fragment type is ``installation``.
Here are some examples.

::

   magic-folder now requires Python 3.8 or newer.

::

   magic-folder now requires pynacl.

Configuration Changes
`````````````````````

The fragment type is ``configuration``.

Removed Features
````````````````

These are changes which deprecate or remove a feature.
The fragment type is ``removed``.

Here are some examples.

::

   All HTTP API endpoints beneath ``/v1`` are now deprecated in favor of the ``/v2`` endpoints.

::

   The HTTP API, deprecated since v1.2.3 in favor of the OTP API, has been removed.


Other Changes
`````````````

These are changes that don't easily fit into another category.
The fragment type is ``other``.


Misc/Other
``````````

The fragment type is ``minor``.

These fragments should be empty.
Their contents will not be included in the assembled news file.

.. _review the changes:

Reviewing Changes
-----------------

First and foremost,
the reviewer's job is to ensure the objective of the corresponding ticket has been satisfied.

Some specific areas to which a reviewer can pay attention:

* Is the implementation unnecessarily difficult for a human reader to understand
  (and maintain)?
* Does the test suite make correct assertions about the behavior of the code under test?
* Does the documentation (developer- and user-facing) accurately describe the new behavior?

Beyond these areas there are a number of mechanical checks applied by the continuous integration system.
Changes should only be accepted if all of these mechanical checks pass
*or* if there are failures which are certainly unrelated to the changes and for which tickets have been filed.

.. _merged into the main branch:

Merging Changes
---------------

Use the GitHub merge button to merge changes to main.
Merge changes if they pass the mechanical continuous integration checks and the softer reviewer guidelines above.

.. _the style guidelines from the Twisted project: https://twistedmatrix.com/trac/wiki/ReviewProcess#Newsfiles
