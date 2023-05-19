Local HTTP Interface
====================

.. _http-api:

HTTP API
--------

Magic-Folder exposes all of its supported functionality in an HTTP API.
The address of the HTTP server is part of the :ref:`config`.

A client may learn how to connect by reading the file ``api_client_endpoint`` from the state directory.
This contains a Twisted "endpoint-string", like ``tcp:localhost:1234``.
The file ``api_client_endpoint`` merely exports information and changes to it will not be reflected in the daemon.


Authorization
~~~~~~~~~~~~~

The HTTP API is protected by a Bearer token-style authorization scheme.
Only requests which include the correct token will receive successful responses.
Other requests will receive **401 Unauthorized** responses which omit the requested resource.
The token value should be included with the **Bearer** scheme in the **Authorization** header element.
For example::

  Authorization: Bearer abcdefghijklmnopqrstuvwxyz

The correct token value can be found in the ``api_token`` file inside the Magic-Folder daemon configuration directory.
The token value is periodically rotated so clients must be prepared to receive an **Unauthorized** response even when supplying the token.
In this case,
the client should re-read the token from the filesystem to determine if the value held in memory has become stale.


Versioning and Stability
~~~~~~~~~~~~~~~~~~~~~~~~

This API is **not considered stable** yet (as of January 2023).

The HTTP API has a global version, indicated in the path: ``/v1/`` for example is Version 1.

APIs put in this hierarchy shall be considered stable (but not yet complete backwards compatilibty, see above caveat).

There is also an ``/experimental`` hierarchy to allow for experimentation, even when Version 1 is considered stable.
APIs in the ``/experimental/`` tree may change in any revision.

Whenever a new version is added (or changed), this section will be updatd and the relevant API(s) will indicate what version adds which features.
A mechanism to add deprecation of APIs will be added in a future release.

 - **Version 1 (``/v1``)**: initial version of the API (not yet considered 100% stable).


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
            "scan_interval": 60,
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
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

The response is delayed until the participant is correctly added to the Collective.
The ``personal_dmd`` is a Tahoe read-only directory capability-string (the participant device holds the write-capability).

.. warning::

   This is a "low-level" API requiring careful handling of the secret
   Personal capability string. A higher-level API using a secure
   magic-wormhole connection is available with the ``.../invite`` and
   ``.../join`` endpoints

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


GET ``/v1/magic-folder/<folder-name>/recent-changes``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Takes an optional ``?number=`` argument (default is 30).
Returns a list of the most-recent changes, like::

    [
        {
            "relpath": "rel/path/foo",
            "modified": 12345,
            "last-updated": 12345,
            "conflicted": false
        },
        # ...
    ]

The results will be reverse-chronological on ``"last-updated"``.


GET ``/v1/magic-folder/<folder-name>/tahoe-objects``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a list of integers representing the sizes of all individual capabilities that this folder is using.
That means a size for each Snapshot capability and its corresponding metadata capability and content capability.
The list is flat; if there are 2 Snapshots on the grid this will return 6 integers.


GET ``/v1/magic-folder/<folder-name>/conflicts``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a ``dict`` of all conflicts in the given magic-folder.
Each item in the ``dict`` maps a relpath to a list of author-names.
The author-names correspond to the device that conflicts with this file.
There will also be a file named like ``<relpath>.conflict-<author-name>`` in the magic-folder whose contents match those of the conflicting remote file.


GET ``/v1/magic-folder/<folder-name>/scan-local``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Request an immediate scan of the local filesystem for the given folder.
Returns an empty ``dict`` after the scan is complete.


GET ``/v1/magic-folder/<folder-name>/poll-remote``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Request an immediate scan of the Collective and remote participants of the given folder.
Returns an empty ``dict`` after the scan is complete.


POST ``/experimental/magic-folder/<folder-name>/invite``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a new invite.
The body of the invite is a JSON object containing the keys:

* ``participant-name``: maps to a string describing what to call the invitee when they join
* ``mode``: ``"read-write"`` or ``"read-only"`` indicating what access the new participant has

This will initiate the invite and returns the serialized invite.
To await the end of the invite process, see the :ref:`invite-wait` endpoint.

A serialized invite is a JSON object that has keys:

* ``id``: A UUID, like ``92148d89-85ae-4677-8629-8ef6de54417d``
* ``participant-name``: the name to call the invitee in the Collective
* ``consumed``: True if the wormhole code has been used up
* ``success``: True if the invite has completed successfully
* ``wormhole-code``: None or the text wormhole code

.. _invite-wait:

POST ``/experimental/magic-folder/<folder-name>/invite-wait``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wait for an invite to complete (either successfully or not).

The body of the invite is a JSON object with keys:

* ``id``: the UUID of the invite to await

This endpoint returns 200 OK with the serialized Invite (see above) if the invite concluded successfully.
Otherwise, the endpoint returns a 400 error describing the error.


POST ``/experimental/magic-folder/<folder-name>/join``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Join a magic-folder by accepting an invite.
The body of the request is a JSON object with keys:

* ``invite-code``: the wormhole code
* ``local-directory``: absolute path of an existing local directory to synchronize files in
* ``author``: arbitrary, valid author name
* ``poll-interval``: seconds between remote update checks
* ``scan-interval``: seconds between local update checks

The endpoint returns 201 Created once the folder is created and joined.
Otherwise, a 400 error is returned describing the error.


POST ``/experimental/magic-folder/<folder-name>/invites``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

List all invites.
Invites are stored in memory only, so this is any active or completed invites since the prorgam started.


.. _status-api:

Status API
----------

There is a WebSocket-based status API located at ``/v1/status``.
This is authenticated the same way as the HTTP API with an ``Authorization:`` header (see above).

All messages are JSON.
Every message looks like this::

    {
        "events": []
    }

...where the ``events`` list contains some non-zero number of event messages.
The first message, upon connect, will likely contain many events: enough to give a consistent view of the current state.
Thereafter, most messages will include only a single event (although clients should handle any number).

Every event has a ``"kind"`` key describing what sort of message it is.
Events will contain other keys; clients should be tolerant of keys in the state they don't understand.

The client doesn't send any messages to the server; it is an error to do so.

The follow event kinds are understood (see ``status.py`` for more details on the sending side, and ``cli.py`` for an example of receiving them):

- ``"scan-completed"``: has key ``timestamp`` which is a unix-timestamp saying when we last looked for local changes.

- ``"poll-completed"``: has a key ``timestamp`` describing when we last asked for remote changes.

- ``"tahoe-connection-changed"``: describes the status of our connected Tahoe-LAFS client: ``connected`` and ``desired`` are the number of servers we are conencted to (and how many we want). Whether we are currently connected to enough is in a boolean ``happy``.

- ``"error-occurred"``: An error, with ``folder`` (the name for the affected folder) ``timestamp`` and ``summary`` (human-readable string).

- ``"folder-added"``: Key ``folder`` says which folder was added.

- ``"folder-left"``: Key ``folder`` says which folder has gone away.

- ``"upload-queued"``: some file (``relpath``) in a folder (``folder``) is queued for upload since ``timestamp``.

- ``"upload-started"``: some file (``relpath``) in a folder (``folder``) has begun upload since ``timestamp``. An ``upload-queued`` event will always preceed this.

- ``"upload-finished"``: a file (``relpath``) in a folder (``folder``) has completed at ``timestamp``. An ``upload-started`` will always preceed this.

- ``"download-queued"``: same as upload version.

- ``"download-started"``: same as upload version.

- ``"download-finished"``: same as upload version.

All timestamps are "seconds since the Unix epoch", as numbers (JSON only has "numbers" and doesn't distinguish floats from ints).

Note that the first "update events" message received will _not_ contain all the updates to that point; it will synthesize the correct events to communicate the current state.
For example, if there are 50 files in the folder and 48 have already been uploaded, there will be just 2 ``upload-queued`` events (because the other 48 have all finished already).
If one of these files is currently being uploaded, there will also be a ``upload-started`` event.
To know the state of all files, use the other endpoints.
