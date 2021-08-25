.. -*- coding: utf-8 -*-

.. _conflicts:

Audience
========

This document is aimed at programmers working on magic-folder. It is a proposed design.


Motivation
==========

Under normal operation, magic-folders scans other participants' Snapshots and reflects those changes locally; sometimes, these changes can conflict.

It is desirable to have an explicit API for noticing and resolving conflicts.
The core of magic-folders operation with users is the filesystem.
This makes the filesystem also an API and conflicts must be part of that API.
The command-line interface shall have sub-commands for listing and resolving conficts; these commands will use the HTTP APIs.


Filesystem API for Conflicts and Resolution
===========================================

When a Snapshot is found to conflict with a particluar local file (say ``foo``) a "conflict file" is written beside it, reflecting the other participants' content (``foo.conflict-laptop``).
That is, ``foo.conflict-laptop`` indicates that the participant "``laptop``" has a conflicting update
The file ``foo.conflict-laptop`` will contain the downloaded ``.content`` of "``laptop``"'s Snapshot.
The content of ``foo`` remains what it was when the conflict was detected.
(Note that when multiple participants exist it's possible to have multiple ``*.confict-*`` files pertaining to a single local file).

List Conflicts
~~~~~~~~~~~~~~

One can use normal directory-browsing tools such as ``ls`` to notice conflict files.


Resolve a Conflict
~~~~~~~~~~~~~~~~~~

When _all_ ``<relpath>.confict-*`` files for a given root are deleted, the conflict is deemed resolved.
The resolution is whatever the contents of ``<relpath>`` are currently.

So, to resolve a conflict as "take theirs", one could run: ``mv foo.conflict-laptop foo`` if there was a single conflict from participant "``laptop``".

To resolve a conflict as "take mine", one simply deletes ``foo.conflict-laptop`` if there was a single conflict from participant "``laptop``".



HTTP API for Conflicts and Resolution
=====================================


``GET /v1/magic-folder/<folder-name>/conflicts``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a list (possibly empty) of local filesystem paths corresponding to each Snapshot that is currently in a Conflict state in the given magic-folder.

Our content is in the path itself.
The conflicting "other" content is in ``<path>.conflict-<name>`` where ``<name>`` is the petname of the participant who is provided the conflicted content.

This endpoint returns a JSON dict mapping any local conflicted ``relpath`` to a list of authors.
Following this example::

    {
        "foo": ["laptop"]
    }

This indicates that a single file ``foo`` has a conflict with a single other participant ``laptop``.



``POST /v1/magic-folder/<folder-name>/resolve-conflict
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A JSON body is passed to this endpoint, following this example::

    {
        "relpath": "local/path",
        "resolution": "author"
    }

The ``relpath`` key is required.
It must be a filesystem path relative to the selected magic-folder.

The ``resolution`` key is required.
It must be the name of a current participant in the given magic-folder.

It is an error if the given ``relpath`` in the given magic-folder is not currently in a conflicted state.

If the ``resolution`` is our user-name then all conflict files are deleted new (local) Snapshot is created (with parents corresponding to all conflicting participants).

If instead the resolution is some other participant, then the content of ``<relpath>.conflict-<participant>`` is moved to ``<relpath>`` and any other conflict files are deleted.
Then a new (local) Snapshot is created (with parents corresponding to all conflicting participants).

The response is delayed until the local state tracking the new Snapshot has been created.

The response code is **CREATED** and the **Content-Type** is ``application/json``.

The response body follows the form of this example::

  {}
