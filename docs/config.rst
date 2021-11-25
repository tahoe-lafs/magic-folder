.. -*- coding: utf-8 -*-

.. _config:

Configuration Storage
=====================

If you are looking for the description of the current
configuration-storage mechanisms, skip down to :ref:`the current
storage description<current>`.


Context and History
-------------------

`magic-folder` originally was extracted from Tahoe-LAFs where it was a
sub-command / sub-system. Thus, it inherited Tahoe's method of
configuration. This is a menagerie of various files in a "node
directory" including a `tahoe.cfg` INI-style file. There is a
`private/` sub-directory which often has "extra-private" information
(like secret keys).


Although a magic-folder is necessarily tied to a Tahoe-LAFS client we
don't *need* to store our configuration there.

We have decided that a user upgrading from a `tahoe magic-folder`
style Magic Folder to a stand-alone Magic Folder will run
`magic-folder migrate`. This allows us the freedom to store our
configuration in any way we like.


Legacy Configuration and State
------------------------------

In versions of Tahoe-LAFS that had magic-folder enabled, the following
configuration could be used:

- `enabled` (bool) from `tahoe.cfg [magic_folder]`
- `local.directory` (from `tahoe.cfg [magic_folder]`)
- `poll_interval` (from `tahoe.cfg [magic_folder]`)

- "magic folders" dict from
  `<node_dir>/private/magic_folders.yaml`. This uses magic-folder
  "names" as keys with the following valid keys as sub-dicts:
  - `directory`: local path to the "magic folder"
  - `poll_interval`: how often to poll (in seconds)
  - `collective_dircap`: read-cap of the shared directory (list of users)
  - `upload_dircap`: write-cap of our mutable directory
  - `umask`: the umask to use for downloaded files

- `<node_dir>/private/magicfolder_<name>.sqlite` the local sqlite
  database for tracking state

Related, Tahoe-LAFS has a token in `<node_dir>/private/api_auth_token`
which is required to access parts of the Web API. This isn't directly
part of magic-folder's configuration (however, standalone magic-folder
will also use such a token).


What Needs to be Stored
-----------------------

As we are currently re-designing the storage mechanism, some of this
might change. However, based on current understanding the state we
WANT to store is:

- for each magic-folder (indexed by "name"):
  - local directory path (the "magic folder")
  - an author:
    - name
    - private_key (32 bytes, a NaCl signing key)
  - a write-cap of our mutable directory
  - some state (see below)
- the node-directory of our Tahoe-LAFS client
  - we may need the `private/api_auth_token`
  - we will need the `node.url` (at least)
  - (it MAY be sufficient to only store the API endpoint (the contents
    of `node.url`), not node-directory. If it's feasible to only store
    the API endpoint, we should do that instead of caring about the
    node-directory. It would probably still be convenient to refer to
    a Tahoe client by node-directory (but we can just pull out the
    Web-API endpoint). If we only store the endpoint and the Tahoe
    config changes, the user would also have to change their
    magic-folder config.
- our own API endpoint (a Twisted server endpoint-string,
  e.g. `tcp:1234` or `unix:/tmp/foo`)

State that we need to store:

- a secret token that protects the API (only users who can read the
  file can access the endpoint)
- list of other participants (per magic-folder) (see note)
- local cache of remote snapshots (probably per-magic folder)
- local copy of snapshots we haven't yet uploaded (probably per-magic folder)
  - metadata details of the snapshot
  - "stash directory" for file-data of the snapshot

NOTE: Currently the "list of other participants" comes from the
"collective dircap" but in the Leif Design each user decides whom they
wish to pull updates from. So, the "list of other participants" above
might be "a readcap of a directory" (and we would determine the actual
list at runtime by reading that capability).


UI to Change the Config
-----------------------

Writing configuration files is hard, error-prone and not a great
interface. It also makes it hard (or impossible) to change where or
how configuration is stored. All configuration shall be changed by an
HTTP API. There will also be CLI commands that are thin wrappers of
the HTTP API. GUI front-ends could use the command-line or the HTTP
API. Nothing except the HTTP API should be touching the actual stored
configuration.

(Of course we can't stop users from editing the config files
themselves, but this should be discouraged).


.. _current:

Where and How To Store the Configuration
----------------------------------------

Somewhat mirroring Tahoe-LAFS's design, configuration shall all be
found in a single directory. This directory can be specified via
command-line option. (Should we have a default? Seems sensible, most
OSes have a reasonably well-defined place for programs to store
configuration data).

All global configuration and state shall be stored in an SQLite3
database. This database will have tables for global configuration and
state.

The "configuration directory" will look like this:

- `confdir` (e.g. `~/.config/magic-folder`)
  - `global.sqlite`
  - `api_token`: 32 bytes of binary data
  - `magic_folder.pid`: contains the PID of the currently-running process

The `global.sqlite` database will have the following tables:

- "version" with a single row containing "1"
- "magic_folders"
  - columns:
  - `name` is a unique unicode string the user provides to identify this folder
  - `location` is a local path for that folder's configuration
- "config"
  - columns:
  - `api_endpoint` is a Twisted server string description
  - `tahoe_node_directory` is the node-directory of our Tahoe-LAFS
    client

    Note: Ideally, we'd only need the Web-API URI however that can be
    configured in tahoe to be randomly-assigned on startup and so we
    should read the URI from `node.url`. There may be other
    configuration we've neglected (since magic-folder used to be
    inside Tahoe) so this also allows us to fix that later too.

Each "magic folder" location is a directory containing the
stash-directory and state for that magic-folder. It will look like
this:

- `<magic folder location>/`
  - `README`: a file containing information about magic-folder
  - `state.sqlite`: the state database
  - `api_client_endpoint`: a Twisted endpoint-string describing how to connect to the HTTP API
  - `api_token`: the token required to authenticate to the HTTP API
  - `folder-<hash>`: one subdirectory for each magic-folder in the daemon
    - `stash/`: the stash-directory containing LocalSnapshot content
    - `state.sqlite`: state database for this magic-folder

The `state.sqlite` for each magic-folder shall contain the following
tables:

- "version" (will always contain 1 row)
  - column:
  - "version" is an int, currently `1`
- "config" (will always contain 1 row)
  - columns:
  - "author_name" is a string of unicode
  - "author_private_key" is a 32-byte blob (a NaCl Signing key)
  - "stash_path" is a local path where we stash data awaiting upload
  - "collective_dircap" is a read-capability-string which defines the magic-folder
  - "upload_dircap" is a write-capability-string defining where we put our snapshots
  - "magic_directory" is a local path to the synchronized directory
  - "poll_interval" says how often (in seconds) to poll for updates
- "local_snapshots"
  - whatever https://github.com/LeastAuthority/magic-folder/issues/197 says
