.. -*- coding: utf-8 -*-

.. _conflicts:

Audience
========

This document is aimed at programmers working on magic-folder. It is a proposed design.


Motivation
==========

It is desirable to have an explicit API for dealing with conflicts.
This allows UI programs to be sure they're accurately indicating intent to magic-folders.


HTTP API for Conflicts and Resolution
=====================================



``GET /v1/conflicts/<folder-name>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a list (possibly empty) of local filesystem paths corresponding to each Snapshot that is currently in a Conflict state in the given magic-folder.

Our content is in the path itself.
The conflicting "other" content is in ``<path>.conflict-<name>`` where ``<name>`` is the petname of the user who is provided the conflicted content.

Justification: we need somewhere for "theirs" versus "my" content .. I think we should still reflect this on the filesystem, even if the *API to manipulate it* is no longer there.
This makes it more obvious for CLI users that they should check the conflicts list; the only alternative would seem to be "run some command occasionally to check for conflicts".
I personally will forget to run this command.


``POST /v1/resolve_conflict/<folder-name>?path=<some-path>&resolution=<theirs|mine>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``path`` query argument is required.
It must be a filesystem path relative to the selected magic-folder.

The ``resolution`` query argument is required.
It must be either the string ``theirs`` or the string ``mine``.

It is an error if the given ``path`` in the given magic-folder is not currently in a conflicted state. In this case the response code is **404 Not Found** (XXX is this appropriate?)

If the resolution is ``theirs`` then the file at ``<path>.theirs.<name>`` is moved to ``<path>`` and a new (local) Snapshot is created (with two parents).

If instead the resolution is ``mine`` then the file at ``<path>.theirs.<name>`` is deleted and a new (local) Snapshot is created (with two parents).

The response is delayed until the local state tracking the new Snapshot has been created.

The response code is **CREATED** and the **Content-Type** is ``application/json``.

The response body follows the form of this example::

  {}
