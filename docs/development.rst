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
#. Development work proceeds in a branch (typically created from a recent master branch revision).
   Prior to this development or in parallel with it,
   communication within the team helps identify and clear roadblocks early.
#. When development has progressed sufficiently,
   the branch is `proposed for review`_.
#. One or more other developers `review the changes`_.
#. The previous two steps may repeat a number of times until the changes pass review.
#. Upon passing review,
   the changes are `merged into the master branch`_.


.. _An issue is filed:

Filing Issues
-------------

The project uses `GitHub issues <https://github.com/leastauthority/magic-folder/issues>`_ for issue tracking.

.. _proposed for review:

Proposing Changes
-----------------

To propose a change,
create a `GitHub Pull Request <https://github.com/leastauthority/magic-folder/pulls>`_ against master.

.. _review the changes:

Reviewing Changes
-----------------

A number of things should be true before a change is merged into the master branch.
These things are checked by automation to the greatest extent possible.
Part of a reviewers job is to verify that the automation has signed off on the changes ("CI is green").
The first part of the reviewer's job, however, is to ensure the intent of the ticket is satisfied by the changes.


  *

.. _merged into the master branch:

Merging Changes
---------------
