Interface
=========

HTTP API
--------

Magic-Folder exposes all of its supported functionality in an HTTP API.
The address of the HTTP server is part of the `daemon configuration`_.

Authorization
~~~~~~~~~~~~~

The HTTP API is protected by Bearer token-style authorization scheme.
Only requests which include the correct token will receive successful responses.
Other requests will receive **401 Unauthorized** responses which omit the requested resource.
The token value should be included with the **Bearer** scheme in the **Authorization** header element.
For example::

  Authorization: Bearer abcdefghijklmnopqrstuvwxyz

The correct token value can be found in the *api_token* file inside the Magic-Folder daemon configuration directory.
The token value is periodically rotated so clients must be prepared to receive an **Unauthorized** response even when supplying the token.
In this case,
the client should re-read the token from the filesystem to determine if the value held in memory has become stale.

.. _`daemon configuration`: :ref:`config`

``GET /v1/magic-folder``
~~~~~~~~~~~~~~~~~~~~~~~~

This endpoint returns a list of all individual magic-folders managed by this daemon.

The response code **OK** and the **Content-Type** is ``application/json``.
The response body follows the form of this example::

  { "folders":
    [ { "name": "Alice's music", "local-path": "/home/alice/Music" }
    , { "name": "Secret docs", "local-path": /home/alice/secrets" }
    ]
  }

``GET /v1/snapshot``
~~~~~~~~~~~~~~~~~~~~

Retrieve information about all snapshots known to exist.

The response is **OK** with an ``application/json`` **Content-Type**::

  {"folder name": {
      "foo/bar": [
        { "type": "local"
        , "identifier": "06be2d83-2d86-402d-ae2a-81b3779d72d9"
        , "parents":
	  [ {"local": "30803885-ef3c-4645-85e6-6b1c9dfd50c3"}
	  , {"remote": "URI:..."}
	  ]
        }
      ]
  }}

Properties of the top-level object map the name of a magic-folder to second-level objects describing snapshots for each file in that folder.
Properties of second-level objects map relative file paths to lists of snapshots for that magic-folder.
Elements of the list are objects with describe a single snapshot.

Local snapshots are represented like this::

  { "type": "local"
  , "identifier": "0d585a0e-c39c-4dec-affb-cbab34245370"
  , "parents": [{"local": "a3eb3d57-5272-45f3-ba5f-04a52024785b"}]
  ,
  }

The parents property gives a list of references to snapshots which are parents of the containing snapshot.
A local snapshot reference in this list is represented like this::

  { "local": "a3eb3d57-5272-45f3-ba5f-04a52024785b" }

The values for the ``local`` property can be resolved against the ``identifier`` described above.

In the future,
this this may also contain remote snapshot references.
A remote snapshot reference in this list is represented like this::

  { "remote": "URI:..." }

The value is a Tahoe-LAFS capability string for a stored object representing the snapshot.

``GET /v1/snapshot/<folder-name>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Not yet implemented.
Get all snapshots for one folder.

``GET /v1/snapshot/<folder-name>?path=<some-path>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Not yet implemented.
Get all snapshots for one folder beneath a certain path.

``POST /v1/snapshot/<folder-name>?path=<some-path>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a new snapshot for a certain file in a certain magic-folder.

The ``path`` query argument is required.
It must be a filesystem path relative to the selected magic-folder.
A new snapshot will be created for the file it identifies.

The response is delayed until the local state tracking the snapshot has been created.

The response code **CREATED** and the **Content-Type** is ``application/json``.
The response body follows the form of this example::

  {}
