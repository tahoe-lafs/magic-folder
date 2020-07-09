.. -*- coding: utf-8 -*-

.. _config:

Configuration Storage
=====================

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
how configuration is stored. All configuration shall be changed by
command-line (GUI front-ends can use this command-line too).


Where and How To Store the Configuration
----------------------------------------

Somewhat mirroring Tahoe-LAFS's design, configuration shall all be
found in a single directory. This directory can be specified via
command-line option. (Should we have a default? Seems sensible, most
OSes have a reasonably well-defined place for programs to store
configuration data).

Data shall be stored in flat files. Data shall be serialized as JSON,
unless otherwise specified. JSON shall be pretty-printed with 4-space
indents and newlines.

The "configuration directory" will look like this:

- `confdir` (e.g. `~/.config/magic-folder`)
  - `global_config.json`
    - "endpoint": a Twisted server-endpoint string
    - "tahoe_node_directory": a path to our Tahoe client's node-dir
    - ... can be expanded for any global config
  - `api_token`: 32 bytes of binary data
  - `magic_folders/` subdir (for state)
    - `arbitrary_name_of_folder/`
      - `config.json`
        - "author":
          - "name": arbitrary name
          - "private_key": base64-encoded signing key
        - "poll_interval": int, how often to check for updates
        - "stash_directory": path to our local-snapshot stash directory
        - ... can be expanded for any per-magic-folder config
      - `local_snapshots.sqlite`: snapshots we haven't yet uploaded
      - `remote_snapshots.sqlite`: cache of remote snapshot data (ours and other users)
