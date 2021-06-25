# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functions and types that implement snapshots
"""
from __future__ import (
    absolute_import,
    division,
    print_function,
)

import os
import json
import time
import base64
from tempfile import mkstemp
from uuid import (
    UUID,
    uuid4,
)
import attr

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    returnValue,
)
from twisted.web.client import (
    FileBodyProducer,
)

from eliot import (
    start_action,
    register_exception_extractor,
)
from eliot.twisted import (
    inline_callbacks,
)

from nacl.signing import (
    SigningKey,
    VerifyKey,
)
from nacl.encoding import (
    Base64Encoder,
)

from .util.encoding import normalize

# version of the snapshot scheme
SNAPSHOT_VERSION = 1


@attr.s
class RemoteAuthor(object):
    """
    Represents the author of a RemoteSnapshot.

    :ivar unicode name: author's name

    :ivar nacl.signing.VerifyKey verify_key: author's public key
    """

    name = attr.ib(converter=normalize)
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
    name = attr.ib(validator=[attr.validators.instance_of(unicode)])
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
    Create a new local author with a freshly generated private (signing) key.

    :param unicode name: the name of this author

    :return LocalAuthor: A new ``LocalAuthor`` with the given name and a
        randomly generated private key.
    """
    signing_key = SigningKey.generate()
    return LocalAuthor(
        name,
        signing_key,
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


def _snapshot_signature_string(name, content_capability, metadata_capability):
    """
    Formats snapshot information into bytes to sign

    :param unicode name: arbitrary snapshot name

    :param bytes content_capability: Tahoe immutable capability-string

    :param bytes metadata_capability: Tahoe immutable capability-string

    :returns: bytes
    """
    snapshot_string = (
        u"magic-folder-snapshot-v1\n"
        u"{content_capability}\n"
        u"{metadata_capability}\n"
        u"{name}\n"
    ).format(
        content_capability=content_capability.decode("ascii"),
        metadata_capability=metadata_capability.decode("ascii"),
        name=name,
    )
    return snapshot_string.encode("utf8")


def sign_snapshot(local_author, snapshot_name, content_capability, metadata_capability):
    """
    Signs the given snapshot with provided author

    :param LocalAuthor local_author: the author to sign the data with

    :param unicode snapshot_name: mangled snapshot name to sign

    :param bytes content_capability: the Tahoe immutable
        capability-string of the actual snapshot data.

    :param bytes metadata capability: the Tahoe immutable
        capability-string of the metadata (which is serialized JSON)

    :returns: instance of `nacl.signing.SignedMessage` (or exception on
        error).
    """
    # XXX Our cryptographers should look at this scheme; see
    # https://github.com/LeastAuthority/magic-folder/issues/190
    data_to_sign = _snapshot_signature_string(
        snapshot_name,
        content_capability,
        metadata_capability,
    )
    return local_author.signing_key.sign(data_to_sign)


def verify_snapshot_signature(remote_author, alleged_signature, content_capability, metadata_capability, snapshot_name):
    """
    Verify the given snapshot.

    :returns: True on success or exception otherwise
    """
    # See comments about "data_to_sign" in sign_snapshot
    data_to_verify = _snapshot_signature_string(
        snapshot_name,
        content_capability,
        metadata_capability,
    )
    return remote_author.verify_key.verify(
        data_to_verify,
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
    identifier = attr.ib(
        validator=attr.validators.instance_of(UUID),
        default=attr.Factory(uuid4),
    )

    def get_content_producer(self):
        """
        :returns: an IBodyProducer that gives you all the bytes of the
            on-disc content. Raises an error if we already have a
            capability. Note that this data will have been stashed previously.
        """
        return FileBodyProducer(self.content_path.asBytesMode("utf-8").open("rb"))

    def to_json(self):
        """
        Serialize the LocalSnapshot to JSON.

        :returns: A JSON string representation of the LocalSnapshot
        """
        # Recursively serialize into one object.

        def _serialized_dict(local_snapshot):
            serialized = {
                'name': local_snapshot.name,
                'metadata': local_snapshot.metadata,
                'identifier': unicode(local_snapshot.identifier),
                'content_path': local_snapshot.content_path.path,
                'parents_local': [
                    _serialized_dict(parent)
                    for parent
                    in local_snapshot.parents_local
                ],
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
                identifier=UUID(hex=snapshot_dict["identifier"]),
                metadata=snapshot_dict["metadata"],
                content_path=FilePath(snapshot_dict["content_path"]),
                parents_local=[
                    deserialize_dict(parent, author)
                    for parent
                    in snapshot_dict["parents_local"]
                ],
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

    :ivar RemoteAuthor author: The author of this snapshot.

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

    @inline_callbacks
    def fetch_content(self, tahoe_client, writable_file):
        """
        Fetches our content from the grid, returning an IBodyProducer?
        """
        yield tahoe_client.stream_capability(self.content_cap, writable_file)


@inline_callbacks
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
        snapshot_data = yield tahoe_client.directory_data(snapshot_cap)
        snapshot = snapshot_data["children"]

        # NB: once we communicate authors + public-keys some other
        # way, we could iterate all our known authors and attempt to
        # verify a signature against the snapshot + metdata
        # capabilities at this point .. that might be better anyway?
        # (A key advantage is not even trying to deserialize anything
        # that's not verified by a signature).
        metadata_cap = snapshot["metadata"][1]["ro_uri"]
        author_signature = snapshot["metadata"][1]["metadata"]["magic_folder"]["author_signature"]

        metadata_json = yield tahoe_client.download_file(metadata_cap)
        metadata = json.loads(metadata_json)

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

        # create SnapshotAuthor
        author = create_author_from_json(metadata["author"])

        # verify the signature
        signature = base64.b64decode(author_signature)
        verify_snapshot_signature(author, signature, content_cap, metadata_cap, name)

        # find all parents
        parent_caps = metadata["parents"]

    returnValue(
        RemoteSnapshot(
            name=name,
            author=author,
            metadata=metadata,
            content_cap=content_cap,
            parents_raw=parent_caps,
            capability=snapshot_cap.decode("ascii"),
        )
    )


@inline_callbacks
def create_snapshot(name, author, data_producer, snapshot_stash_dir, parents=None,
                    raw_remote_parents=None, modified_time=None):
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

    :param int modified_time: timestamp to use as last-modified time
        (or None for "now")
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
            parents_remote.append(parent.capability)
        else:
            raise ValueError(
                "Parent {} is type {} not LocalSnapshot or RemoteSnapshot".format(
                    idx,
                    type(parent),
                )
            )
    if raw_remote_parents:
        parents_remote.extend(raw_remote_parents)

    chunk_size = 1024*1024  # 1 MiB
    chunks_per_yield = 100

    # 1. create a temp-file in our stash area
    temp_file_fd, temp_file_name = mkstemp(
        prefix="snap",
        dir=snapshot_stash_dir.asBytesMode("utf-8").path,
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

    now = modified_time or int(time.time())
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


def format_filenode(cap, metadata=None):
    """
    Create the data structure Tahoe-LAFS uses to represent a filenode.

    :param bytes cap: The read-only capability string for the content of the
        filenode.

    :param dict: Any metadata to associate with the filenode (or None
        to exclude it entirely).

    :return: The Tahoe-LAFS representation of a filenode with this
        information.
    """
    node = {
        u"ro_uri": cap,
    }
    if metadata is not None:
        node[u"metadata"] = metadata
    return [
        u"filenode",
        node,
    ]



@inline_callbacks
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
            parents_raw.append(parent)#.capability)

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

    # create our metadata
    snapshot_metadata = {
        "snapshot_version": SNAPSHOT_VERSION,
        "name": snapshot.name,
        "author": snapshot.author.to_remote_author().to_json(),
        "modification_time": snapshot.metadata["mtime"],
        "parents": [
            parent_cap.encode("utf8")
            for parent_cap in parents_raw
        ]
    }
    metadata_cap = yield tahoe_client.create_immutable(
        json.dumps(snapshot_metadata)
    )

    # sign the snapshot (which can only happen after we have the
    # content-capability and metadata-capability)
    author_signature = sign_snapshot(author_key, snapshot.name, content_cap, metadata_cap)
    author_signature_base64 = base64.b64encode(author_signature.signature)

    # create the actual snapshot: an immutable directory with
    # some children:
    # - "content" -> RO cap (arbitrary data)
    # - "metadata" -> RO cap (json)

    data = {
        u"content": format_filenode(content_cap, snapshot.metadata),
        u"metadata": format_filenode(
            metadata_cap, {
                u"magic_folder": {
                    u"author_signature": author_signature_base64,
                },
            },
        ),
    }

    snapshot_cap = yield tahoe_client.create_immutable_directory(data)

    # Note: we still haven't updated our Personal DMD to point at this
    # snapshot, so we shouldn't yet delete the LocalSnapshot
    returnValue(
        RemoteSnapshot(
            name=snapshot.name,
            author=snapshot.author.to_remote_author(),
            metadata=snapshot_metadata,
            parents_raw=parents_raw,
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
register_exception_extractor(TahoeWriteException, lambda e: {"code": e.code, "body": e.body})
