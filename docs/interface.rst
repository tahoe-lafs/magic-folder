Interface
=========

HTTP API
--------

Magic-Folder exposes all of its supported functionality in an HTTP API.
The address of the HTTP server is part of the `daemon configuration`_.

A client may learn how to connect by reading the file `api_client_endpoint` from the state directory.
This contains a Twisted "endpoint-string", like `tcp:localhost:1234`.
The file `api_client_endpoint` merely exports information and changes to it will not be reflected in the daemon.


Authorization
~~~~~~~~~~~~~

The HTTP API is protected by a Bearer token-style authorization scheme.
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

This endpoint returns a dict of all individual magic-folders managed by this daemon.
The keys of the dict are the folder name and the values are themselves dicts.

You may include the query argument ``?include_secret_information=1`` to include values for each folder which should be kept hidden (and are not shown by default).
These are: ``upload_dircap``, ``collective_dircap``, and the ``signing_key`` inside ``author``.

The response code **OK** and the **Content-Type** is ``application/json``.
The response body follows the form of this example (containing a single magic-folder named "documents")::

    {
        "documents": {
            "name": "documents",
            "author": {
                "name": "alice",
                "verify_key": "OY7FCVPCOJXDNHQLSDTTJFONTROMQQED5Q6K33T3NBGGQHKLV73Q===="
            },
            "poll_interval": 60,
            "is_admin": true,
            "stash_path": "/home/alice/.config/magic-folder/documents",
            "magic_path": "/home/alice/Documents"
        }
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


``POST /v1/magic-folder/<folder-name>/snapshot?path=<some-path>``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a new snapshot for a certain file in a certain magic-folder.

The ``path`` query argument is required.
It must be a filesystem path relative to the selected magic-folder.
A new snapshot will be created for the file it identifies.

The response is delayed until the local state tracking the snapshot has been created.

The response code **CREATED** and the **Content-Type** is ``application/json``.
The response body follows the form of this example::

  {}


``GET /v1/magic-folder/<folder-name>/participants``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

List all participants in a certain magic-folder.

The response is **OK** with an ``application/json`` **Content-Type**::

    {
        "participant name": {
            "personal_dmd": "URI:DIR2-RO:..."
        }
    }

There will be one entry per participant.
``personal_dmd`` is a Tahoe read-only directory capability-string.


``POST /v1/magic-folder/<folder-name>/participants``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a new participant to a certain magic-folder.
Accepts a JSON body listing the details of the participant to add::

    {
        "author": {
            "name": "arbitrary string"
        },
        "personal_dmd": "URI:DIR2-RO:..."
    }

The response is delayed until the participant is correctly added to the Collective DMD.
The ``personal_dmd`` is a Tahoe read-only directory capability-string (the participant device holds the write-capability).
A response code of **CREATED** is sent upon success with response body::

    {}


``GET /v1/magic-folder/<folder-name>/file-status``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retrieve the file-status of every file in a given magic-folder.
The response is **OK** with an ``application/json`` **Content-Type**::

    [
        {
            "relpath": "rel/path/foo",
            "mtime": 12345,
            "size": 321
        },
        {
            "relpath": "rel/path/bar",
            "mtime": 12346,
            "size": 111
        }
    ]

There will be one entry in the list for every file.
The list is ordered from most-recent to least-recent timestamp.
``relpath`` is the relative path in the magic-folder.
``mtime`` is in seconds.
``size`` is in bytes.


GET `/v1/magic-folder/<folder-name>/tahoe-objects`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a list of integers representing the sizes of all individual capabilities that this folder is using.
That means a size for each Snapshot capability and its corresponding metadata capability and content capability.
The list is flat; if there are 2 Snapshots on the grid this will return 6 integers.


GET `/v1/magic-folder/<folder-name>/conflicts`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a ``dict`` of all conflicts in the given magic-folder.
Each item in the ``dict`` maps a relpath to a list of author-names.
The author-names correspond to the device that conflicts with this file.
There will also be a file named like ``<relpath>.conflict-<author-name>`` in the magic-folder whose contents match those of the conflicting remote file.


GET `/v1/magic-folder/<folder-name>/scan-local`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Request an immediate scan of the local filesystem for the given folder.
Returns an empty `dict` after the scan is complete.


GET `/v1/magic-folder/<folder-name>/poll-remote`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Request an immediate scan of the Collective DMD and remote participants of the given folder.
Returns an empty `dict` after the scan is complete.



Status API
----------

There is a WebSocket-based status API located at ``/v1/status``.
This is authenticated the same way as the HTTP API with an ``Authorization:`` header (see above).

All messages are JSON.
Upon connecting, a new client will immediately receive a "state" message::

    {
        "state": {
            "folders": {
                "default": {
                    "downloads": [],
                    "errors": [],
                    "uploads": [],
                    "recent": [
                        {
                            "relpath": "foo"
                            "conflicted": false,
                            "modified": 1634431697,
                            "last-updated": 1634431700
                        }
                    ]
                }
            },
            "synchronizing": false,
            "tahoe": {
                "connected": 3,
                "happy": true,
                "desired": 2
            },
            "scanner": {
                "last-scan": 1634431700.1234
            },
            "poller": {
                "last-poll": null
            }
        }
    }

After that the client may receive further state updates with a ``"state"`` message like the above.
Currently the only valid kind of message is ``"state"``.
The above example has no uploads or downloads happening and a single recent file, ``foo``.

The state for each folder consists of the following information:

- ``"synchronizing"``: ``true`` or ``false``. When ``true`` the
  magic-folder daemon is uploading data to or downloading data from
  Tahoe-LAFS.
- ``"tahoe"``: a dict containing status information about the Tahoe-LAFS connection
  - ``"connected"``: the number of storage-servers the client is connected to
  - ``"desired"``: the number of storage-servers we want to connect to
  - ``"happy"``: ``true`` if ``"connected"`` is greater than the client's configured "happy"
- ``"folders"`` contains keys mapping the folder name to:
  - ``"uploads"`` and ``"downloads"`` contain currently queued or active uploads (or downloads).
    Each ``dict`` in these lists contain:
    - ``"relpath"``: the relative-path
    - ``"queued-at"``: the Unix timestamp when this item was queued
    - ``"started-at"``: the Unix timestamp when we started uploading (or downloading) this item
      This key will not exist until we do start.
  - ``"recent"`` contains a list up to 30 of the most-recently updated files.
    Each ``dict`` in this list contains:
    - ``"relpath"``: the relative path of this item
    - ``"modified"``: the Unix timestamp when the on-disk file was most-recently modified
    - ``"last-updated"``: the Unix timestamp when this item's state was updated in the magic-folder
    - ``"conflicted"``: a boolean indicating if there is a conflict for this relative path
  - ``"scanner"`` contains information about the local changes scanner
    - ``"last-scan"``: ``null`` if no scan is completed yet, or the timestamp of the last completion
  - ``"poller"`` contains information about the remote changes poller
    - ``"last-poll"``: ``null`` if no scan is completed yet, or the timestamp of the last completion

Clients should be tolerant of keys in the state they don't understand.
Unknown state keys should be ignored.
Note that ``"modified"`` is when the local state for thie item changed while ``"last-updated"`` is to do with the filesystem modification time.
For example, a file may have an on-disk modification time that is older than the last time we updated our state about it, especially one that came from another device.

The client doesn't send any messages to the server; it is an error to do so.
