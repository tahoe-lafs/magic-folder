# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functions and types that implement snapshots
"""
from __future__ import print_function

import os
import time
import json
import base64
from tempfile import mkstemp

import attr

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.web.client import (
    FileBodyProducer,
)

import magic_folder

from eliot import (
    start_action,
    register_exception_extractor,
)

from nacl.signing import (
    SigningKey,
    VerifyKey,
)
from nacl.encoding import (
    Base64Encoder,
)

# version of the snapshot scheme
SNAPSHOT_VERSION = 1


@attr.s
class RemoteAuthor(object):
    """
    Represents the author of a RemoteSnapshot.

    :ivar unicode name: author's name

    :ivar nacl.signing.VerifyKey verify_key: author's public key
    """

    name = attr.ib()
    verify_key = attr.ib(validator=[attr.validators.instance_of(VerifyKey)])

    def to_json(self):
        """
        :return: a representation of this author in a dict suitable for
            JSON encoding (see also create_author_from_json)
        """
        return {
            "name": self.name,
            "verify_key": self.verify_key.encode(encoder=Base64Encoder),
        }


@attr.s
class LocalAuthor(object):
    """
    Represents the author of a LocalSnapshot.

    :ivar name: author's name

    :ivar nacl.signing.SigningKey signing_key: author's private key
    """

    name = attr.ib()
    signing_key = attr.ib(validator=[attr.validators.instance_of(SigningKey)])

    # NOTE: this should not be converted to JSON or serialized
    # (because it contains a private key), it is only for signing
    # LocalSnapshot instances (as they're uploaded).

    @property
    def verify_key(self):
        """
        :returns: the VerifyKey corresponding to our signing key
        """
        return self.signing_key.verify_key

    def to_remote_author(self):
        """
        :returns: a RemoteAuthor instance. This will be the same, but have
            only a verify_key corresponding to our signing_key
        """
        return create_author(self.name, self.signing_key.verify_key)


def create_local_author(name):
    """
    Create a new local author with a freshly generated private
    (signing) key. This author will not be saved on disk anywhere; see
    `write_local_author` to do that.

    :param name: the name of this author
    """
    signing_key = SigningKey.generate()
    return LocalAuthor(
        name,
        signing_key,
    )


def write_local_author(local_author, magic_folder_name, config):
    """
    Writes a LocalAuthor instance beside other magic-folder data in the node-directory
    """
    key_fname = "magicfolder_{}.privkey".format(magic_folder_name)
    path = config.get_config_path("private", key_fname)
    keydata_base64 = local_author.signing_key.encode(encoder=Base64Encoder)
    author_data = {
        "author_name": local_author.name,
        "author_private_key": keydata_base64,
    }
    with open(path, "w") as f:
        json.dump(author_data, f)


def create_local_author_from_config(config, name=None):
    """
    :param config: a Tahoe config instance (created via `allmydata.client.read_config`)

    :param name: which Magic Folder to use (or 'default')

    :returns: a LocalAuthor instance from our configuration
    """
    # private-keys go in "<node_dir>/private/magicfolder_<name>.privkey"
    # to mirror where the sqlite database goes
    if name is None:
        name = "default"
    nodedir = config.get_config_path()
    magic_folders = magic_folder.load_magic_folders(nodedir)
    if name not in magic_folders:
        raise RuntimeError(
            "No magic-folder named '{}'".format(name)
        )

    # we will always have authorship information for this
    # magic-folder; "legacy" magic-folders will go through "tahoe
    # migrate" first and have an author created.

    author_raw = config.get_private_config("magicfolder_{}.privkey".format(name))
    author_data = json.loads(author_raw)

    return LocalAuthor(
        name=author_data[u"author_name"],
        signing_key=SigningKey(
            author_data[u"author_private_key"],
            encoder=Base64Encoder,
        ),
    )


def create_author(name, verify_key):
    """
    :param name: arbitrary name for this author

    :param verify_key: a NaCl VerifyKey instance

    :returns: a RemoteAuthor instance.
    """
    return RemoteAuthor(
        name=name,
        verify_key=verify_key,
    )


class UnknownPropertyError(ValueError):
    """
    Creating a RemoteAuthor from a JSON object failed because an unknown
    property was included in the JSON.
    """


class MissingPropertyError(ValueError):
    """
    Creating a RemoteAuthor from a JSON object failed because a required
    property was missing from the JSON.
    """


def create_author_from_json(data):
    """
    Deserialize a RemoteAuthor.

    :param data: a dict containing "name" and "verify_key". "name"
        maps to a unicode string and "verify_key" maps to a unicode
        string in base64 format (that decodes to 32 bytes of key
        data). Usually these data would be obtained from
        `RemoteAuthor.to_json`.

    :returns: a RemoteAuthor instance from the given data (which
       would usually come from RemoteAuthor.to_json())
    """
    permitted_keys = required_keys = ["name", "verify_key"]
    for k in data.keys():
        if k not in permitted_keys:
            raise UnknownPropertyError(k)
    for k in required_keys:
        if k not in data:
            raise MissingPropertyError(k)
    verify_key = VerifyKey(data["verify_key"], encoder=Base64Encoder)
    return create_author(data["name"], verify_key)


def sign_snapshot(local_author, snapshot, content_capability):
    """
    Signs the given snapshot with provided author

    :param LocalAuthor local_author: the author to sign the data with

    :param LocalSnapshot snapshot: snapshot to sign

    :param str content_capability: the Tahoe immutable
        capability-string of the actual snapshot data.

    :returns: bytes representing the signature (or exception on
        error).
    """
    # XXX See
    # https://github.com/LeastAuthority/magic-folder/issues/190 as
    # this is likely insufficient (and hasn't been looked at by our
    # cryptograhphers yet).
    data_to_sign = (
        u"{content_capability}\n"
        u"{name}\n"
    ).format(
        content_capability=content_capability,
        name=snapshot.name,
    )
    return local_author.signing_key.sign(data_to_sign.encode("utf8"))


def verify_snapshot_signature(remote_author, alleged_signature, content_capability, snapshot_name):
    """
    Verify the given snapshot.

    :returns: True on success or exception otherwise
    """
    # See comments about "data_to_sign" in sign_snapshot
    data_to_verify = (
        u"{content_capability}\n"
        u"{name}\n"
    ).format(
        content_capability=content_capability,
        name=snapshot_name,
    )
    return remote_author.verify_key.verify(
        data_to_verify.encode("utf8"),
        alleged_signature,
    )


@attr.s
class LocalSnapshot(object):
    """
    :ivar FilePath content_path: The filesystem path to our stashed contents.

    :ivar [LocalSnapshot] parents_local: The parents of this snapshot that are
        only known to exist locally.

    :ivar [bytes] parents_remote: The capability strings of snapshots that are
        known to exist remotely.
    """
    name = attr.ib()
    author = attr.ib()
    metadata = attr.ib()
    content_path = attr.ib(validator=attr.validators.instance_of(FilePath))
    parents_local = attr.ib(validator=attr.validators.instance_of(list))
    parents_remote = attr.ib(
        default=attr.Factory(list),
        validator=attr.validators.instance_of(list),
    )

    def get_content_producer(self):
        """
        :returns: an IBodyProducer that gives you all the bytes of the
            on-disc content. Raises an error if we already have a
            capability. Note that this data will have been stashed previously.
        """
        return FileBodyProducer(self.content_path.open("rb"))

    def to_json(self):
        """
        Serialize the LocalSnapshot to JSON.

        :returns: A JSON string representation of the LocalSnapshot
        """
        # Recursively serialize into one object.

        def _serialized_dict(local_snapshot):
            serialized = {
                'name' : local_snapshot.name,
                'metadata' : local_snapshot.metadata,
                'content_path' : local_snapshot.content_path.path,
                'parents_local' : [ _serialized_dict(parent) for parent in local_snapshot.parents_local ]
            }

            return serialized

        serialized = _serialized_dict(self)

        return (json.dumps(serialized))

    @classmethod
    def from_json(cls, serialized, author):
        """
        Creates a LocalSnapshot from a JSON serialized string that represents the
        LocalSnapshot.

        :param str serialized: the JSON string that represents the LocalSnapshot

        :param author: an instance of LocalAuthor

        :returns: A LocalSnapshot object representation of the JSON serialized string.
        """
        local_snapshot_dict = json.loads(serialized)

        def deserialize_dict(snapshot_dict, author):
            name = snapshot_dict["name"]

            return cls(
                name=name,
                author=author,
                metadata=snapshot_dict["metadata"],
                content_path=FilePath(snapshot_dict["content_path"]),
                parents_local=[ deserialize_dict(parent, author) for parent in snapshot_dict["parents_local"] ],
            )

        return deserialize_dict(local_snapshot_dict, author)


@attr.s
class RemoteSnapshot(object):
    """
    Represents a snapshot corresponding to a particular version of a
    file authored by a particular human.

    :ivar unicode name: the name of this Snapshot. This is a mangled
        path relative to our local magic-folder path.

    :ivar dict metadata: a dict containing metadata about this
        Snapshot. Usually these are unicode keys mapping to data that
        can be anything JSON can serialize (so text, numbers, booleans
        or lists and dicts of the same).

    :ivar parents_raw: list of capability-strings of our
        parents. Capability-strings are bytes.

    :ivar author: SnapshotAuthor instance

    :ivar bytes capability: an immutable CHK:DIR2 capability-string.

    :ivar bytes content_cap: a capability-string for the actual
        content of this RemoteSnapshot. Use `fetch_content()` to
        retrieve the contents.
    """

    name = attr.ib()
    author = attr.ib()  # any SnapshotAuthor instance
    metadata = attr.ib()
    capability = attr.ib()
    parents_raw = attr.ib()
    content_cap = attr.ib()

    @inlineCallbacks
    def fetch_parent(self, tahoe_client, parent_index):
        """
        Download the given parent, creating a RemoteSnapshot.

        :param tahoe_client: the client to use

        :param parent_index: which parent to fetch

        :returns: a RemoteSnapshot instance.
        """
        capability = self.parents_raw[parent_index]
        snapshot = yield create_snapshot_from_capability(capability, tahoe_client)
        returnValue(snapshot)

    @inlineCallbacks
    def fetch_content(self, tahoe_client, writable_file):
        """
        Fetches our content from the grid, returning an IBodyProducer?
        """
        yield tahoe_client.stream_capability(self.content_cap, writable_file)


@inlineCallbacks
def create_snapshot_from_capability(snapshot_cap, tahoe_client):
    """
    Create a RemoteSnapshot from a snapshot capability string

    :param tahoe_client: the Tahoe client to use

    :param str capability_string: unicode data representing the
        immutable CHK:DIR2 directory containing this snapshot.

    :return Deferred[Snapshot]: RemoteSnapshot instance on success.
        Otherwise an appropriate exception is raised.
    """

    action = start_action(
        action_type=u"magic_folder:tahoe_snapshot:create_snapshot_from_capability",
    )
    with action:
        snapshot_json = yield tahoe_client.download_capability(snapshot_cap)
        snapshot = json.loads(snapshot_json)

        # create SnapshotAuthor
        author_cap = snapshot["author"][1]["ro_uri"]
        author_json = yield tahoe_client.download_capability(author_cap)
        snapshot_author = json.loads(author_json)

        author = create_author_from_json(snapshot_author)

        verify_key = VerifyKey(snapshot_author["verify_key"], Base64Encoder)
        metadata = snapshot["content"][1]["metadata"]["magic_folder"]

        if "snapshot_version" not in metadata:
            raise Exception(
                "No 'snapshot_version' in snapshot metadata"
            )
        if metadata["snapshot_version"] != SNAPSHOT_VERSION:
            raise Exception(
                "Unknown snapshot_version '{}' (not '{}')".format(
                    metadata["snapshot_version"],
                    SNAPSHOT_VERSION,
                )
            )

        name = metadata["name"]
        content_cap = snapshot["content"][1]["ro_uri"]

        # verify the signature
        signature = base64.b64decode(metadata["author_signature"])
        verify_snapshot_signature(author, signature, content_cap, name)

        # find all parents
        parents = [k for k in snapshot.keys() if k.startswith('parent')]
        parent_caps = [snapshot[parent][1]["ro_uri"] for parent in parents]

        returnValue(
            RemoteSnapshot(
                name=name,
                author=create_author(
                    name=snapshot_author["name"],
                    verify_key=verify_key,
                ),
                metadata=metadata,
                content_cap=content_cap,
                parents_raw=parent_caps,
                capability=snapshot_cap.decode("ascii"),
            )
        )


@inlineCallbacks
def create_snapshot(name, author, data_producer, snapshot_stash_dir, parents=None):
    """
    Creates a new LocalSnapshot instance that is in-memory only. All
    data is stashed in `snapshot_stash_dir` before this function
    returns.

    :param name: The name for this snapshot (usually the 'mangled' filename).

    :param author: LocalAuthor instance (which will have a valid
        signing-key)

    :param data_producer: file-like object that can deliver all the
        data for the content of this Snapshot (it will be read
        immediately).

    :param FilePath snapshot_stash_dir: the directory where Snapshot contents
        are to be stashed.

    :param parents: a list of LocalSnapshot instances (may be empty,
        which is the default if not specified).
    """
    if parents is None:
        parents = []

    if not isinstance(author, LocalAuthor):
        raise ValueError(
            "create_snapshot 'author' must be a LocalAuthor instance"
        )

    # separate the two kinds of parents we can have (LocalSnapshot or
    # RemoteSnapshot)
    parents_local = []
    parents_remote = []

    for idx, parent in enumerate(parents):
        if isinstance(parent, LocalSnapshot):
            parents_local.append(parent)
        elif isinstance(parent, RemoteSnapshot):
            parents_remote.append(parent)
        else:
            raise ValueError(
                "Parent {} is type {} not LocalSnapshot or RemoteSnapshot".format(
                    idx,
                    type(parent),
                )
            )

    chunk_size = 1024*1024  # 1 MiB
    chunks_per_yield = 100

    # 1. create a temp-file in our stash area
    temp_file_fd, temp_file_name = mkstemp(
        prefix="snap",
        dir=snapshot_stash_dir.path,
    )
    try:
        # 2. stream data_producer into our temp-file
        done = False
        while not done:
            for _ in range(chunks_per_yield):
                data = data_producer.read(chunk_size)
                if data:
                    if len(data) > 0:
                        os.write(temp_file_fd, data)
                else:
                    done = True
                    break
            yield
    finally:
        os.close(temp_file_fd)

    now = time.time()
    returnValue(
        LocalSnapshot(
            name=name,
            author=author,
            metadata={
                "ctime": now,
                "mtime": now,
            },
            content_path=FilePath(temp_file_name),
            parents_local=parents_local,
            parents_remote=parents_remote,
        )
    )


def format_filenode(cap, metadata):
    """
    Create the data structure Tahoe-LAFS uses to represent a filenode.

    :param bytes cap: The read-only capability string for the content of the
        filenode.

    :param dict: Any metadata to associate with the filenode.

    :return: The Tahoe-LAFS representation of a filenode with this
        information.
    """
    return [
        "filenode", {
            "ro_uri": cap,
            "metadata": metadata,
        },
    ]


def format_author_filenode(cap):
    """
    Create a Tahoe-LAFS filenode data structure that represents a Magic-Folder
    author.

    :param bytes cap: The read-only capability string to the object containing
        the author information.

    :return: The Tahoe-LAFS representation of a filenode with this
        information.
    """
    return format_filenode(cap, {})


def format_snapshot_filenode(cap, metadata):
    """
    Create a Tahoe-LAFS filenode data structure that represents a Magic-Folder
    snapshot.

    :param bytes cap: The read-only capability string to the object containing
        the snapshot content.

    :return: The Tahoe-LAFS representation of a filenode with this
        information.
    """
    return format_filenode(
        cap,
        {
            # XXX FIXME timestamps are bogus
            "ctime": 1202777696.7564139,
            "mtime": 1202777696.7564139,
            "magic_folder": metadata,
            "tahoe": {
                "linkcrtime": 1202777696.7564139,
                "linkmotime": 1202777696.7564139
            }
        },
    )


def format_dirnode(cap):
    """
    Create the data structure Tahoe-LAFS uses to represent a dirnode.

    :param bytes cap: The read-only capability string for the object holding
        the directory information.

    :return: The Tahoe-LAFS representation of a dirnode.
    """
    return [
        "dirnode", {
            "ro_uri": cap,
            # is not having "metadata" permitted?
            # (ram) Yes, looks like.
        }
    ]

@inlineCallbacks
def write_snapshot_to_tahoe(snapshot, author_key, tahoe_client):
    """
    Writes a LocalSnapshot object to the given tahoe grid. Will also
    (recursively) upload any LocalSnapshot parents.

    :param LocalSnapshot snapshot: the snapshot to upload.

    :param SigningKey author_key: a NaCl SigningKey corresponding to
        the author who will sign this snapshot (and also any
        LocalSnapshots that are parents of this one).

    :returns: a RemoteSnapshot instance
    """
    # XXX probably want to give this a progress= instance (kind-of
    # like teh one in Tahoe) so we can track upload progress for
    # status-API for GUI etc.

    # XXX might want to put all this stuff into a queue so we only
    # upload X at a time etc. -- that is, the "real" API is probably a
    # high-level one that just queues up the upload .. a low level one
    # "actually does it", including re-tries etc. Currently, this
    # function is both of those.

    parents_raw = [] # raw capability strings

    if len(snapshot.parents_remote):
        for parent in snapshot.parents_remote:
            parents_raw.append(parent.capability)

    # we can't reference any LocalSnapshot objects we have, so they
    # must be uploaded first .. we do this up front so we're also
    # uploading the actual content of the parents first.
    if len(snapshot.parents_local):
        # if parent is a RemoteSnapshot, we are sure that its parents
        # are themselves RemoteSnapshot. Recursively upload local parents
        # first.
        to_upload = snapshot.parents_local[:]  # shallow-copy the thing we'll iterate
        for parent in to_upload:
            parent_remote_snapshot = yield write_snapshot_to_tahoe(parent, author_key, tahoe_client)
            parents_raw.append(parent_remote_snapshot.capability)
            snapshot.parents_local.remove(parent)  # the shallow-copy to_upload not affected

    # upload the content itself
    content_cap = yield tahoe_client.create_immutable(snapshot.get_content_producer())

    # sign the snapshot (which can only happen after we have the content-capability)
    author_signature = sign_snapshot(author_key, snapshot, content_cap)
    author_signature_base64 = base64.b64encode(author_signature.signature)
    author_data = snapshot.author.to_remote_author().to_json()

    author_cap = yield tahoe_client.create_immutable(
        json.dumps(author_data)
    )
    # print("author_cap: {}".format(author_cap))

    # create the actual snapshot: an immutable directory with
    # some children:
    # - "content" -> RO cap (arbitrary data)
    # - "author" -> RO cap (json)
    # - "parent0" -> RO cap to a Snapshot
    # - "parentN" -> RO cap to a Snapshot

    # XXX actually, should we make the parent pointers a sub-dir,
    # maybe? that might just be extra complexity for no gain, but
    # "parents/0", "parents/1" aesthetically seems a bit nicer.


    content_metadata = {
        "snapshot_version": SNAPSHOT_VERSION,
        "name": snapshot.name,
        "author_signature": author_signature_base64,
    }
    data = {
        "content": format_snapshot_filenode(content_cap, content_metadata),
        "author": format_author_filenode(author_cap),
    }

    # XXX 'parents_remote1 are just Tahoe capability-strings for now
    for idx, parent_cap in enumerate(parents_raw):
        data[u"parent{}".format(idx)] = format_dirnode(parent_cap)

    # print("data: {}".format(data))
    snapshot_cap = yield tahoe_client.create_immutable_directory(data)

    # XXX *now* is the moment we can remove the LocalSnapshot from our
    # local database -- so if at any moment before now there's a
    # failure, we'll try again.
    returnValue(
        RemoteSnapshot(
            # XXX: we are copying over the name from LocalSnapshot, it is not
            # stored on tahoe at the moment. This means, when we read back a snapshot
            # we cannot create a RemoteSnapshot object from a cap string.
            name=snapshot.name,
            author=create_author(  # remove signing_key, doesn't make sense on remote snapshots
                name=snapshot.author.name,
                verify_key=snapshot.author.verify_key,
            ),
            metadata=content_metadata,  # XXX not authenticated by signature...
            parents_raw=parents_raw,  # XXX FIXME (at this point, will have parents' immutable caps .. parents don't ork yet)
            capability=snapshot_cap.decode("ascii"),
            content_cap=content_cap,
        )
    )


class TahoeWriteException(Exception):
    """
    Something went wrong while doing a `tahoe put`.
    """
    def __init__(self, code, body):
        self.code = code
        self.body = body

    def __str__(self):
        return '<TahoeWriteException code={} body="{}">'.format(
            self.code,
            self.body,
        )


# log exception caused while doing a tahoe put API
register_exception_extractor(TahoeWriteException, lambda e: {"code": e.code, "body": e.body })
