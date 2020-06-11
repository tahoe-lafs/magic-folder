# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

import json

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.web.client import (
    FileBodyProducer,
)
from hyperlink import (
    DecodedURL,
)

import attr

from .common import (
    get_node_url,
)


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

    # XXX this is "kind-of" the start of prototyping "a Python API to Tahoe"
    # XXX for our immediate use, we need something like:

    @inlineCallbacks
    def create_immutable_directory(self, directory_data):
        post_uri = self.url.replace(
            path=(u"uri",),
            query=[(u"t", u"mkdir-immutable")],
        )
        res = yield self.http_client.post(
            post_uri.to_text(),
            json.dumps(directory_data),
        )
        capability_string = yield res.content()
        returnValue(
            capability_string.strip()
        )

    @inlineCallbacks
    def create_immutable(self, producer):
        # XXX should handle "any" producer, taking a shortcut for now
        raw_data = None
        if isinstance(producer, FileBodyProducer):
            raw_data = producer._inputFile.read()
        elif hasattr(producer, "read"):
            raw_data = producer.read()
        else:
            raise NotImplementedError()

        put_uri = self.url.replace(
            path=(u"uri",),
            query=[(u"mutable", u"false")],
        )
        res = yield self.http_client.put(
            put_uri.to_text(),
            data=raw_data,
        )
        capability_string = yield res.content()
        returnValue(
            capability_string.strip()
        )

    @inlineCallbacks
    def download_capability(self, cap):
        get_uri = self.url.replace(
            path=(u"uri",),
            query=[(u"uri", cap.decode("ascii"))],
        )
        res = yield self.http_client.get(get_uri.to_text())
        data = yield res.content()
        returnValue(data)

    @inlineCallbacks
    def stream_capability(self, cap, filelike):
        get_uri = self.url.replace(
            path=(u"uri",),
            query=[(u"uri", cap.decode("ascii"))],
        )
        res = yield self.http_client.get(get_uri.to_text())
        yield res.collect(filelike.write)


@inlineCallbacks
def create_tahoe_client(node_directory, treq_client=None):
    """
    Create a new TahoeClient instance that is speaking to a particular
    Tahoe node.

    XXX is treq_client= enough of a hook to get a 'testing' treq
    client?.
    """

    # real:
    # client = create_tahoe_client(tmpdir)

    # testing:
    # root = create_fake_tahoe_root()
    # client = create_tahoe_client(tmpdir, treq_client=create_tahoe_treq_client(root))

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
