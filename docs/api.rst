
.. _http-api:

HTTP API
========

The Magic Folder Daemon provides an HTTP-based API.
Most other ``magic-folder`` sub-commands interact with the Daemon by using this API.
Other kinds of UIs or local programs can use the HTTP API directly or the Python command-line as they see fit.


Authentication
--------------

Users must be able to prove that they have read access to a local file stored in the Magic Folder Daemon configuration.
This file is `api_token` at the root of a Magic Folder Daemon configuration.

A client wishing to communicate to the Magic Folder Daemon encodes the token in an `Authorization: ` header sent to the Web server.
This header's value is `Bearer <token>`.
The `api_token` file is already encoded correctly for inclusion directly in this header.


Endpoints
---------

In general, endpoints accept and return JSON data.
The `GET` HTTP verb is used for retieving information and `POST` is used when modifying information.
Details are shown in particular endpoints.
Everything is rooted at `/v1/`.
A client may learn how to connect by reading the file `api_client_endpoint` from the state directory.
This contains a Twisted "endpoint-string", like `tcp:localhost:1234`.
The file `api_client_endpoint` merely exports information and changes to it will not be reflected in the daemon.


GET `/v1/magic-folder`
~~~~~~~~~~~~~~~~~~~~~~

List all magic-folders currently configured.
An optional query-argument (`?include_secret_information=1`) may be included to request the secret capabilities to be included as well.
Returns JSON like::

    {
        "folders: [
            {
                "name": "funny-pictures",
                "author": {
                    "name": "alice",
                    "verify_key": "base32-encoded key",
                },
                "stash_path": "local/filesystem/path",
                "magic_path": "local/magic/folder/path",
                "poll_interval": 60,
                "is_admin": true
            }
        ]
    }

If `?include_secret_information=1` is included then each magic-folder will also include `author["signing_key"]`, `collective_dircap` and `upload_dircap` keys.


GET `/v1/snapshot`
~~~~~~~~~~~~~~~~~~

Lits all snapshots in all configured magic-folders. Returns JSON like::

    {
        "folder-name": {
            "cat.jpeg": [
                {
                    "parents": [],
                    "identifier": "...",
                    "type": "local",
                    "author": {
                        "name": "bob",
                        "verify_key": "..."
                    },
                    "content-path": ".../bob-magic/funny-pictures/cat.jpeg"
                }
            ]
        }
    }


GET `/v1/snapshot/<folder-name>`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Lits all snapshots in a particular magic-folders. Returns JSON like::

    {
        "cat.jpeg": [
            {
                "parents": [],
                "identifier": "...",
                "type": "local",
                "author": {
                    "name": "bob",
                    "verify_key": "..."
                },
                "content-path": ".../bob-magic/funny-pictures/cat.jpeg"
            }
        ]
    }


POST `/v1/snapshot/<folder-name>?path=<file-name>`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Creates a new snapshot for a particular file inside a particluar magic-folder.
The POST body should be empty in this version of the API.
The `?path=` query argument is required.
`?path=` must point to a file inside the directory of the referenced magic-folder.


GET `/v1/magic-folder/<folder-name>/tahoe-objects`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns a list of integers representing the sizes of all individual capabilities that this folder is using.
That means a size for each Snapshot capability and its corresponding metadata capability and content capability.


PUT `/v1/magic-folder/<folder-name>/scan`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Request an immediate scan of a particular magic-folder.
An empty result will be returned after the scan completes (this means all local snapshots have been created and serialized into the database).
