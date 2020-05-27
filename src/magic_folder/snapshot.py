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


@attr.s
class TahoeClient(object):
    """
    An object that knows how to call a particular tahoe client's
    WebAPI. Usually this means a node-directory (to get the base URL)
    and a treq client (to make HTTP requests).

    XXX probably in a different package than this? Re-factor a couple
    things so that tahoe_mkdir() etc take a 'tahoe_client' (instead of
    a treq + node_dir)?
    """

    # node_directory = attr.ib()
    url = attr.ib()
    http_client = attr.ib()


@inlineCallbacks
def create_tahoe_client(node_directory, treq_client=None):
    """
    Create a new TahoeClient instance that is speaking to a particular
    Tahoe node.

    XXX is treq_client= enough of a hook to get a 'testing' treq
    client?.
    """
    # from allmydata.node import read_config  ??
    base_url = get_node_url(node_directory)
    url = DecodedURL.from_text(base_url)

    if treq_client is None:
        treq_client = HTTPClient(
            agent=BrowserLikeRedirectAgent(),
        )
    client = TahoeClient(
        url=url,
        http_client=treq_client,
    )
    yield  # maybe we want to at least try getting / to see if it's alive?
    returnValue(client)


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

    def sign_snapshot(self, snapshot):
        """
        Signs the given snapshot.

        :param Snapshot snapshot: the Snapshot to sign

        :returns: bytes representing the signature
        """
        assert self.signing_key is not None
        raise NotImplemented


@attr.s
class Snapshot(object):
    """
    Represents a snapshot corresponding to a file.

    :ivar name: the name of this Snapshot. This is a mangled path
        relative to our local magic-folder path.

    :ivar metadata: a dict containing metadata about this Snapshot.

    :ivar parents_raw: list of capablitiy-strings instances of our
        parents

    :ivar author: SnapshotAuthor instance

    :ivar capability: None if this snapshot isn't from (or uploaded
        to) a Tahoe grid yet, otherwise a valid immutable CHK:DIR2
        capability-string.

    XXX "how to get the content" probably needs more thinking .. two
    cases: we're a "from the grid" snapshot, and so need an async
    "download_content()" or something like that

    XXX the other possibility is that we're an "in-memory" Snapshot
    that isn't in any grid yet, so we need some way to stream incoming
    data.

    XXX maybe it's just better to have two kinds of TahoeSnapshots?
    But, it's tempting to say "if from grid", "contents.read()"
    downloads data; "if from local" then "contents.read()" reads data
    from a file-like.

    :ivar contents: a file-like to read the content
    """

    name = attr.ib()
    metadata = attr.ib()
    parents_raw = attr.ib()
    capability = attr.ib()
    contents = attr.ib()  # a file-like object that can stream the content?

    def get_content_producer(self):
        """
        :returns: an IBodyProducer that gives you all the bytes of the
        on-disc content. Raises an error if we already have a
        capability.
        """
        # XXX or, maybe instead of .contents we want "a thing that
        # produces file-like objects" so that e.g. if you call
        # get_content_producer() twice it works..
        return FileBodyProducer(self.contents)

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
def create_snapshot_from_capability(tahoe_client, capability_string):
    """
    Create a snapshot by downloading existing data.

    :param tahoe_client: the Tahoe client to use

    :param str capability_string: unicode data representing the
        immutable CHK:DIR2 directory containing this snapshot.

    :return Deferred[Snapshot]: Snapshot instance on success.
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
    Creates a new Snapshot instance that is in-memory only (call
    write_snapshot() to commit it to a grid).

    :param author: SnapshotAuthor

    :param data_producer: file-like object that can read

    XXX file-like, okay, does it need to suppot random-access? just
    skip-ahead? none of that? Should we pass a 'way to create a
    file-like producer' instead (so e.g. we don't even open the file
    if we never look at the content)?
    """


@inlineCallbacks
def write_snapshot_to_tahoe(snapshot, tahoe_client):
    """
    Writes a Snapshot object to the given tahoe grid.
    """
    # XXX if we're writing the content, then author things as separate
    # capabilities, how do we behave if something goes wrong between
    # making either of those and putting the whole snapshot in?
    # (meejah: I think "ignore it" is fine, because if we just try
    # again then we should get the same capabilities back anyway;
    # worst case is extra writing).

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
        "pubkey": snapshot.author.verify_key,
        "name": snapshot.author.name,
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

    data = {
        "content": [
            "filenode", {
                "ro_uri": content_cap,
                "metadata": {
                    "ctime": 1202777696.7564139,
                    "mtime": 1202777696.7564139,
                    "magic": {
                        "arbitrary": "foo",
                        "magic-folder": "stuff",
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
    content = yield res.content()
    snapshot.capability = content
    returnValue(snapshot)
    # XXX acutally, I kind of like the idea of a "client-side
    # Snapshot" versus a "server-side Snapshot" -- this method could
    # take a ClientSnapshot (which has get_content_producer, no
    # .capability) and return a ServerSnapshot (which has .capability
    # and e.g. a download_content() or similar)


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

@inlineCallbacks
def tahoe_put_immutable(nodeurl, filepath, treq):
    """
    :param DecodedURL nodeurl: The web endpoint of the Tahoe-LAFS client
        associated with the magic-folder client.
    :param FilePath filepath: The file path that needs to be stored into
        the grid.
    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to
        make the queries.
    :return Deferred[unicode]: The readcap associated with the newly created
        unlinked file.
    """
    url = nodeurl.child(
        u"uri",
    ).add(
        u"format",
        u"CHK",
    )

    with start_action(
            action_type=u"magic_folder:tahoe_snapshot:tahoe_put_immutable"):
        put_uri = url.to_uri().to_text().encode("ascii")
        # XXX: Should not read entire file into memory. See:
        # https://github.com/LeastAuthority/magic-folder/issues/129
        with filepath.open("r") as file:
            data = file.read()

        response = yield treq.put(put_uri, data)
        if response.code == OK or response.code == CREATED:
            result = yield readBody(response)
            returnValue(result)
        else:
            body = yield readBody(response)
            raise TahoeWriteException(response.code, body)


@inlineCallbacks
def tahoe_create_snapshot_dir(nodeurl, content, parents, timestamp, treq):
    """
    :param DecodedURL nodeurl: The web endpoint of the Tahoe-LAFS client
        associated with the magic-folder client.
    :param unicode content: readcap for the content.
    :param [unicode] parents: List of parent snapshot caps
    :param integer timestamp: POSIX timestamp that represents the creation time
    :return Deferred[unicode]: The readcap associated with the newly created
        snapshot.
    """

    # dict that would be serialized to JSON
    body = \
    {
        u"content": [ "filenode", { u"ro_uri": content,
                                    u"metadata": { } } ],
        u"version": [ "filenode", { u"ro_uri": str(SNAPSHOT_VERSION),
                                    u"metadata": { } } ],
        u"timestamp": [ "filenode", { u"ro_uri": str(timestamp),
                                      u"metadata": { } } ],
    }

    action = start_action(
        action_type=u"magic_folder:tahoe_snapshot:tahoe_create_snapshot_dir",
    )
    with action:
        # populate parents
        # The goal is to populate the dictionary with keys u"parent0", u"parent1" ...
        # with corresponding dirnode values that point to the parent URIs.
        if parents != []:
            for (i, parent) in enumerate(parents):
                body[u"parent{}".format(i)] = ["dirnode", {u"ro_uri": parent}]

        body_json = json.dumps(body, indent=4)

        # POST /uri?t=mkdir-immutable
        url = nodeurl.child(
            u"uri",
        ).add(
            u"t",
            u"mkdir-immutable"
        )

        post_uri = url.to_uri().to_text().encode("ascii")
        response = yield treq.post(post_uri, body_json)
        if response.code != OK:
            returnValue((yield bad_response(url, response)))

        result = yield readBody(response)
        returnValue(result)
