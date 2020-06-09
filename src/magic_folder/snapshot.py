# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functions and types that implement snapshots
"""
from __future__ import print_function

import io
import os
import time
import json
import base64
from tempfile import mkstemp

import attr
import nacl

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.web.client import (
    BrowserLikeRedirectAgent,
    FileBodyProducer,
)

from .common import (
    get_node_url,
)

from twisted.web.client import (
    readBody,
)

from twisted.web.http import (
    OK,
    CREATED,
)

from hyperlink import (
    DecodedURL,
)

from .common import (
    bad_response,
)

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
class SnapshotAuthor(object):
    """
    Represents the author of a Snapshot.

    :ivar name: author's name

    :ivar nacl.signing.VerifyKey verify_key: author's public key (always available)

    :ivar nacl.signing.SigningKey signing_key: author's private key (if available)
    """

    name = attr.ib()
    verify_key = attr.ib(validator=[attr.validators.instance_of(VerifyKey)])
    signing_key = attr.ib(default=None)

    @signing_key.validator
    def corresponding_key(self, attribute, value):
        if value is not None:
            if not isinstance(value, SigningKey):
                raise ValueError(
                    "'signing_key' must be a nacl.signing.SigningKey"
                )
            if value.verify_key != self.verify_key:
                raise ValueError(
                    "'signing_key' must correspond to the 'verify_key'"
                )

    def to_json_private(self):
        """
        WARNING this represntation will include private information if our
        signing_key is valid.

        :return: a representation of this author in a dict suitable for
            JSON encoding (see also create_author_from_json)
        """
        serialized = {
            "name": self.name,
            "verify_key": self.verify_key.encode(encoder=Base64Encoder),
        }
        if self.signing_key is not None:
            serialized["signing_key"] = self.signing_key.encode(encoder=Base64Encoder)
        return serialized

    def to_json(self):
        """
        :return: a representation of this author in a dict suitable for
            JSON encoding (see also create_author_from_json)
        """
        serialized = self.to_json_private()
        return {
            k: v
            for k, v in serialized.items()
            if k not in ["signing_key"]
        }

    def has_signing_key(self):
        """
        :returns: True if we have a valid signing key, False otherwise
        """
        return self.signing_key is not None

    def _sign_capability_string(self, capability):
        """
        Signs the given snapshot.

        :returns: bytes representing the signature
        """
        # XXX what do we sign? the capability makes some sense -- but
        # then we need to wait until upload time to do the
        # signing...is it okay to have the author private-key in
        # memory always? ("Because Python" I don't think we can do
        # anything anyway and it's on disk so if you can examine
        # memory, you should be able to read a file the user can)

        # XXX also, signing the content capability-string means that
        # all the metadata is NOT under signature .. not clear that's
        # an issue. I guess we could sign some kind of hash of
        # "content_capability + ...."
        if self.signing_key is None:
            raise Exception(
                "Can't sign snapshot, author '{}' has no signing_key".format(
                    self.name,
                )
            )

        # XXX more notes: it doesn't make sense to sign a
        # RemoteSnapshot (it already has to be signed) -- and we can't
        # sign capability-strings unless we do it exactly at
        # upload-time .. so this method is "private" to be used only
        # by the upload machinery to sign the capability *after* we've
        # uploaded the contents (but before creating the
        # immutable-dir)

        sig = self.signing_key.sign(capability)
        return sig


def create_author(name, verify_key=None, signing_key=None):
    """
    :returns: a SnapshotAuthor instance. If no keys are provided, a
        VerifyKey is created.
    """
    if verify_key is not None and not isinstance(verify_key, VerifyKey):
        raise ValueError("verify_key must be a nacl.signing.VerifyKey")
    if signing_key is not None and not isinstance(signing_key, SigningKey):
        raise ValueError("signing_key must be a nacl.signing.SigningKey")

    if signing_key is not None:
        if verify_key is None:
            verify_key = signing_key.verify_key
        if signing_key.verify_key != verify_key:
            raise ValueError(
                "SnapshotAuthor 'verify_key' does not correspond to 'signing_key'"
            )

    if verify_key is None:
        assert signing_key is None, "logic error"
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key

    return SnapshotAuthor(
        name=name,
        verify_key=verify_key,
        signing_key=signing_key,
    )



def create_author_from_json(data):
    """
    :returns: a SnapshotAuthor instance from the given data (which
       would usually come from SnapshotAuthor.to_json())
    """
    permitted_keys = ["name", "signing_key", "verify_key"]
    required_keys = ["name", "verify_key"]
    for k in data.keys():
        if k not in permitted_keys:
            raise ValueError(
                u"Unknown SnapshotAuthor key '{}'".format(k)
            )
    for k in required_keys:
        if k not in data:
            raise ValueError(
                u"SnapshotAuthor requires '{}' key".format(k)
            )
    verify_key = VerifyKey(data["verify_key"], encoder=Base64Encoder)
    try:
        signing_key = SigningKey(data["signing_key"], encoder=Base64Encoder)
    except KeyError:
        signing_key = None
    return create_author(data["name"], verify_key, signing_key)


# XXX see also comments about maybe a ClientSnapshot and a Snapshot or
# so; "uploading" a ClientSnapshot turns it into a Snapshot.

# XXX LocalSnapshot and RemoteSnapshot are both immutable python objects

# XXX need to think about how to represent parents:
# - "parents_raw" was meant to capture "we sometimes only have capability-strings"
# - "lazy" loading ...
# - what about LocalSnapshots? (we want them in parent lists but they have no capability yet)
# - want to avoid recursively loading ALL the versions (until we need to)


# XXX might want to think about an ISnapshot interface?
# - stuff common between LocalSnapshot and RemoteSnapshot
#   - count_parents()
#   - fetch_parent()
#   - name, author, metadata, ...

# XXX: (ram) do we need author info in LocalSnapshot? Isn't it relevant only in RemoteSnapshot?
# XXX: THINK (ram) when will we get capability strings for parents instead of RemoteSnapshots? Never
#      in the case of LocalSnapshots, because we will either be changing (extending) an existing
#      LocalSnapshot in the offline usecase. In that case, our parent is another LocalSnapshot.
#      In the case when our copy is older than one of the other client and we do a fast forward,
#      then we will be fetching RemoteSnapshots recursively until one of the parents is our current
#      snapshot.
@attr.s
class LocalSnapshot(object):
    name = attr.ib()
    author = attr.ib()  # XXX must be "us" / have a signing-key
    metadata = attr.ib()
    content_path = attr.ib()  # full filesystem path to our stashed contents
    parents_remote = attr.ib()  # DECIDE: are these RemoteSnapshots or just capability-strings?
    parents_local = attr.ib()  # LocalSnapshot instances

    def count_parents(self):
        """
        XXX or something
        """
        return len(self.parents_local) + len(self.parents_remote)

    @inlineCallbacks
    def fetch_parent(self, index, tahoe_client):
        """
        Returns all parents as LocalSnapshot or RemoteSnapshot (or a mix)
        instances -- possibly instantiating RemoteSnapshot instances
        from capability-strings.
        """

    def get_content_producer(self):
        """
        :returns: an IBodyProducer that gives you all the bytes of the
        on-disc content. Raises an error if we already have a
        capability.
        """
        # XXX or, maybe instead of .contents we want "a thing that
        # produces file-like objects" so that e.g. if you call
        # get_content_producer() twice it works..
        return FileBodyProducer(
            open(self.content_path, "rb")
        )


@attr.s
class RemoteSnapshot(object):
    """
    Represents a snapshot corresponding to a particular version of a
    file authored by a particular human.

    :ivar name: the name of this Snapshot. This is a mangled path
        relative to our local magic-folder path.

    :ivar metadata: a dict containing metadata about this Snapshot.

    :ivar parents_raw: list of capablitiy-strings instances of our
        parents

    :ivar author: SnapshotAuthor instance

    :ivar capability: an immutable CHK:DIR2 capability-string.
    """

    name = attr.ib()
    author = attr.ib()  # any SnapshotAuthor instance
    metadata = attr.ib()
    capability = attr.ib()
    parents_raw = attr.ib()
    content_cap = attr.ib()

    def count_parents(self):
        """
        XXX or something
        """
        return len(self.parents_raw)

    @property
    def signature(self):
        return self.metadata["author_signature"]

    @inlineCallbacks
    def fetch_parent(self, parent_index, tahoe_client):
        """
        Fetches the given parent.

        :param int parent_index: which parent to fetch

        :param tahoe_client: the Tahoe client to use to retrieve
            capabilities

        :returns: a Snapshot instance or raises an exception
        """
        assert parent_index >= 0 and parent_index < len(self.parents_raw)
        raise NotImplemented

    @inlineCallbacks
    def fetch_content(self, tahoe_client, writable_file):
        """
        Fetches our content from the grid, returning an IBodyProducer?
        """
        # XXX should verify the signature using self.author + signature

        # XXX returns some kind of streaming API to download the content

        # XXX OR it just downloads all the content into memory and returns it?

        # XXX OR you give this a file-like to WRITE into



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
        debug = json.dumps(snapshot, indent=4)

        print("debug: {}".format(debug))
        # create SnapshotAuthor
        author_cap = snapshot["author"][1]["ro_uri"]
        author_json = yield tahoe_client.download_capability(author_cap)
        snapshot_author = json.loads(author_json)

        author = create_author_from_json(snapshot_author)

        verify_key = VerifyKey(snapshot_author["verify_key"], Base64Encoder)
        metadata = snapshot["content"][1]["metadata"]

        name = metadata["magic_folder"]["name"]
        content_cap = snapshot["content"][1]["ro_uri"]

        returnValue(
            RemoteSnapshot(
                name=name,
                author=create_author(
                    name=snapshot_author["name"],
                    verify_key=verify_key,
                ),
                metadata=metadata,
                content_cap=content_cap,
                parents_raw=[], # XXX: This needs to be populated
                capability=snapshot_cap.decode("ascii"),
            )
        )


@inlineCallbacks
def create_snapshot(name, author, data_producer, snapshot_stash_dir, parents):
    """
    Creates a new LocalSnapshot instance that is in-memory only (call
    write_snapshot_to_tahoe() to commit it to a grid). Actually not
    in-memory, we should commit it to local disk / database before
    ever returning LocalSnapshot instance

    :param author: SnapshotAuthor which must have a valid signing-key

    :param data_producer: file-like object that can read

    XXX file-like, okay, does it need to support random-access? just
    skip-ahead? none of that? Should we pass a 'way to create a
    file-like producer' instead (so e.g. we don't even open the file
    if we never look at the content)?

    XXX thinking of local-first, the data_producer here is used just
    once (immediately) to copy all the data into some "staging" area
    local to our node-dir (or at least specified in our confing)
    .. then we have a canonical full path we can "burn in" to the
    LocalSnapshot and it can produce new readers on-demand.
    """
    yield

    if not author.has_signing_key():
        raise ValueError(
            "Author of LocalSnapshot must have signing key"
        )

    parents_remote = []
    parents_local = []
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
    # 1. create a temp-file in our stash area
    temp_file_fd, temp_file_name = mkstemp(
        prefix="snap",
        dir=snapshot_stash_dir,
    )
    try:
        # 2. stream data_producer into our temp-file
        data = data_producer.read(chunk_size)
        if data and len(data) > 0:
            os.write(temp_file_fd, data)
    finally:
        os.close(temp_file_fd)

    # XXX FIXME write snapshot meta-information (including the path
    # temp_file_name) into the snapshot database. TDB

    now = time.time()
    returnValue(
        LocalSnapshot(
            name=name,
            author=author,
            # XXX look at how this becomes metadata in RemoteSnapshot, etc
            # .. we want to overwrite the signature (or .. only add it if
            # missing?)
            metadata={
                "ctime": now,
                "mtime": now,
#                "magic_folder": {
#                    "author_signature": "pending",
#                }
            },
            content_path=temp_file_name,
            parents_remote=parents_remote,
            parents_local=parents_local,
        )
    )

# XXX THINK
# how to do parents?
#
# - LocalSnapshot can only have RemoteSnapshots as parents?
#   -> we could allow both, and the "upload" function has to recursively upload parents
# - RemoteSnapshots only have RemoteSnapshots as parents
#
# offline-first?
# - can we build up multiple LocalSnapshots and upload them later?
# - can we do ^ but maintain them across client re-starts?
# - ideal use-case is:
#   - build a bunch of LocalSnapshots
#   - shut down daemon
#   - restart daemon (series of LocalSnapshots still there)
#   - upload LocalSnapshots, making them RemoteSnapshots
# - have to 'stash' actual contents somewhere (maybe <our dir>/.stash/*)
# - huge PRO of doing ^ first is that our client can crash and not lose snapshots
# - then LocalSnapshot can have LocalSnapshot instances in parents list
#
# tahoe as a library?
# - can we start with TahoeClient and build out?
# - can TahoeClient remain in this repo, get promoted later?
# - ...


@inlineCallbacks
def write_snapshot_to_tahoe(snapshot, tahoe_client):
    """
    Writes a LocalSnapshot object to the given tahoe grid.

    :param snapshot: LocalSnapshot

    :returns: a RemoteSnapshot instance
    """
    # XXX in offline-first case, this must recurse and write any
    # LocalSnapshot parents to the grid FIRST so that it can burn in
    # their capabilities in the parent list when uploading the child.

    # XXX if we're writing the content, then author things as separate
    # capabilities, how do we behave if something goes wrong between
    # making either of those and putting the whole snapshot in?
    # (meejah: I think "ignore it" is fine, because if we just try
    # again then we should get the same capabilities back anyway;
    # worst case is extra writing).

    # XXX need to recursively look at our parents for LocalSnapshots
    # and upload those FIRST.

    # XXX probably want to give this a progress= instance (kind-of
    # like teh one in Tahoe) so we can track upload progress for
    # status-API for GUI etc.

    # XXX might want to put all this stuff into a queue so we only
    # upload X at a time etc. -- that is, this API is probably a
    # high-level one that just queues up the upload .. a low level one
    # "actually does it", including re-tries etc.

    # sanity-checks before we do anything; we must have a
    # SnapshotAuthor here that is capable of signing (because a
    # snapshot must be signed).
    if not snapshot.author.has_signing_key():
        raise ValueError(
            "Can't upload LocalSnapshot: no author signing key"
        )

    # upload the content itself
    content_cap = yield tahoe_client.create_immutable(snapshot.get_content_producer())

    author_signature = snapshot.author._sign_capability_string(content_cap)
    author_signature_base64 = base64.b64encode(author_signature.signature)
    author_data = snapshot.author.to_json()

    author_cap = yield tahoe_client.create_immutable(
        io.BytesIO(json.dumps(author_data))
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

    # XXX FIXME timestamps are bogus

    content_metadata = {
        "name": snapshot.name,
        "author_signature": author_signature_base64,
    }
    data = {
        "content": [
            "filenode", {
                "ro_uri": content_cap,
                "metadata": {
                    "ctime": 1202777696.7564139,
                    "mtime": 1202777696.7564139,
                    "magic_folder": content_metadata,
                    "tahoe": {
                        "linkcrtime": 1202777696.7564139,
                        "linkmotime": 1202777696.7564139
                    }
                }
            },
        ],
        "author": [
            "filenode", {
                "ro_uri": author_cap,
                "metadata": {
                    "ctime": 1202777696.7564139,
                    "mtime": 1202777696.7564139,
                    "tahoe": {
                        "linkcrtime": 1202777696.7564139,
                        "linkmotime": 1202777696.7564139
                    }
                }
            }
        ],
    }
    if len(snapshot.parents_local):
        # if parent is a RemoteSnapshot, we are sure that its parents
        # are themselves RemoteSnapshot. Recursively upload local parents
        # first.
        for parent in snapshot.parents_local:
            parent_remote_snapshot = yield write_snapshot_to_tahoe(parent, tahoe_client)
            snapshot.parents_remote.append(parent_remote_snapshot.capability)

    # XXX 'parents_remote1 are just Tahoe capability-strings for now
    for idx, parent_cap in enumerate(snapshot.parents_remote):
        data[u"parent{}".format(idx)] = [
            "content", [
                "filenode", {
                    "ro_uri": parent_cap,
                    # is not having "metadata" permitted?
                }
            ]
        ]

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
            parents_raw=snapshot.parents_remote,  # XXX FIXME (at this point, will have parents' immutable caps .. parents don't work yet)
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


# XXX FIXME
# use TahoeWriteException in write_snapshot_to_tahoe
# body = yield readBody(response)
# raise TahoeWriteException(response.code, body)
