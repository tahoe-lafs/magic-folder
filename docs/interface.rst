Interface
=========

HTTP Blah Blah
--------------

When enabled ...

``GET /v1/magic-folder``
~~~~~~~~~~~~~~~~~~~~~~~~

This endpoint returns ...

The response is **OK** with an ``application/json`` **Content-Type**::

  { ...
  }

``GET /v1/snapshot``
~~~~~~~~~~~~~~~~~~~~

This endpoint returns information about all snapshots known to exist.

The response is **OK** with an ``application/json`` **Content-Type**::

  {"folder name": {
      "foo/bar": [
        { "type": "local"
        , "identifier": 2
        , "parents": [{"local": 1}, {"remote": "URI:CHK:..."}]
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

``GET /v1/snapshot/:folder-name``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        Get all snapshots for one folder:

``GET /v1/snapshot/:folder-name?path=:some-path``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        Get all snapshots for one folder beneath a certain path:

``POST /v1/snapshot/:folder-name?path=:some-path``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        Create a new snapshot for a file at a certain path in a certain folder:
