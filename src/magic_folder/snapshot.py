# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functions and types that implement snapshots
"""
from __future__ import print_function

import time
import json
import attr

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

# version of the snapshot scheme
SNAPSHOT_VERSION = 1


# XXX what are the keys here? Probably NaCl asymmetric keys.
@attr.s
class SnapshotAuthor(object):
    """
    Represents the author of a Snapshot.

    :ivar name: author's name

    :ivar verify_key: author's public key (always available)

    :ivar signing_key: author's private key (if available)
    """

    name = attr.ib()
    verify_key = attr.ib()
    signing_key = attr.ib(default=None)

    def has_signing_key(self):
        """
        :returns: True if we have a valid signing key, False otherwise
        """
        return self.signing_key is not None

    def sign_snapshot(self, snapshot):
        """
        Signs the given snapshot.

        :param Snapshot snapshot: the Snapshot to sign

        :returns: bytes representing the signature
        """
        assert self.signing_key is not None
        raise NotImplemented


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

@attr.s
class LocalSnapshot(object):
    name = attr.ib()
    author = attr.ib()  # XXX must be "us" / have a signing-key
    metadata = attr.ib()
    capability = attr.ib()
    content_path = attr.ib()  # full filesystem path to our stashed contents
    _parents_raw = attr.ib()  # XXX only capability-strings (of RemoteSnapshot parents)
    _parents_local = attr.ib()  # XXX "or something";

    def count_parents(self):
        """
        XXX or something
        """
        return len(self._parents_raw) + len(self._parents_local)

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

    :ivar capability: None if this snapshot isn't from (or uploaded
        to) a Tahoe grid yet, otherwise a valid immutable CHK:DIR2
        capability-string.
    """

    name = attr.ib()
    author = attr.ib()  # any SnapshotAuthor instance
    metadata = attr.ib()
    capability = attr.ib()
    _parents_raw = attr.ib()


    def count_parents(self):
        """
        XXX or something
        """
        return len(self._parents_raw)

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
def create_snapshot_from_capability(tahoe_client, capability_string):
    """
    Create a snapshot by downloading existing data.

    :param tahoe_client: the Tahoe client to use

    :param str capability_string: unicode data representing the
        immutable CHK:DIR2 directory containing this snapshot.

    :return Deferred[Snapshot]: RemoteSnapshot instance on success.
        Otherwise an appropriate exception is raised.
    """

    action = start_action(
        action_type=u"magic_folder:tahoe_snapshot:create_snapshot",
    )
    with action:
        # XXX this seems .. complex. And also 'unicode' is python2-only
        nodeurl_u = unicode(get_node_url(node_directory.asBytesMode().path), 'utf-8')
        nodeurl = DecodedURL.from_text(nodeurl_u)

        content_cap = yield tahoe_put_immutable(nodeurl, filepath, treq)

        # XXX probably want a reactor/clock passed in?
        now = time.time()

        # HTTP POST mkdir-immutable
        snapshot_cap = yield tahoe_create_snapshot_dir(
            nodeurl,
            content_cap,
            parents,
            now,
            treq,
        )

        returnValue(
            Snapshot(
                snapshot_cap,
                parents,
            )
        )


@inlineCallbacks
def create_snapshot(author, data_producer):
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

    # 1. create a temp-file in our stash area
    temp_file_name = ...
    with open(temp_file_name, "w") as temp_file:

        # 2. stream data_producer into our temp-file
        while not data_producer.empty():
            temp_file.write(data_producer.read(1024))

    # for true "offline-first" we'd write this information to database or similar
    return LocalSnapshot(
        content_path=temp_file_name,  # basic idea: our content is "fixed" now
    )


# XXX THINK
# how to do parents?
#
# - LocalSnapshot can only have RemoteSnapshots as parents?
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

    :returns: a RemoteSnapshop instance
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

    # upload the content itself
    put_uri = tahoe_client.url.replace(
        path=(u"uri",),
        query=[("mutable", "false")],
    )
    res = yield tahoe_client.put(
        put_uri.to_text(),
        data=snapshot.get_content_producer(),
    )
    content_cap = yield res.content()
    print("content_cap: {}".format(content_cap))

    author_data = {
        "name": snapshot.author.name,
        "public_key": snapshot.author.verify_key,
    }
    res = yield treq.put(
        put_uri.to_text(),
        json.dumps(author_data),
    )
    author_cap = yield res.content()
    print("author_cap: {}".format(author_cap))

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

    data = {
        "content": [
            "filenode", {
                "ro_uri": content_cap,
                "metadata": {
                    "ctime": 1202777696.7564139,
                    "mtime": 1202777696.7564139,
                    "magic_folder": {
                        "author_signature": signature,
                    },
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
    # XXX 'parents_raw' are just Tahoe URIs
    for idx, parent_cap in enumerate(snapshot.parents_raw):
        data[u"parent{}".format(idx)] = [
            "content": [
                "filenode", {
                    "ro_uri": parent_cap,
                    # is not having "metadata" permitted?
                }
            ]
        ]


    post_uri = tahoe_client.url.replace(
        path=(u"uri",),
        query=[("t", "mkdir-immutable")],
    )
    res = yield treq.post(post_uri.to_text(), json.dumps(data))
    content_cap = yield res.content()

    # XXX *now* is the moment we can remove the LocalSnapshot from our
    # local database -- so if at any moment before now there's a
    # failure, we can try again.
    returnValue(
        RemoteSnapshot(
            name=snapshot.name,
            author=snapshot.author,
            metadata=snapshot.metadata,
            parents_raw=[],  # XXX FIXME (but now we have all parents' immutable caps
            capability=res.content_cap,
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
