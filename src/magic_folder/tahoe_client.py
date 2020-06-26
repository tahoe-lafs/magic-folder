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
    """

    # node_directory = attr.ib()
    url = attr.ib()
    http_client = attr.ib()

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
        """
        :param producer: can take anything that treq's data= method to
            treq.request allows which is currently: str, file-like or
            IBodyProducer. See
            https://treq.readthedocs.io/en/release-20.3.0/api.html#treq.request
        """

        put_uri = self.url.replace(
            path=(u"uri",),
            query=[(u"mutable", u"false")],
        )
        res = yield self.http_client.put(
            put_uri.to_text(),
            data=producer,
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
def create_tahoe_client(url, http_client):
    """
    Create a new TahoeClient instance that is speaking to a particular
    Tahoe node.

    :param url: the baes URL of the Tahoe instance

    :param http_client: a Treq HTTP client

    :returns: a TahoeClient instance
    """

    client = TahoeClient(
        url=DecodedURL.from_text(base_url),
        http_client=treq_client,
    )
    yield  # maybe we want to at least try getting / to see if it's alive?
    returnValue(client)
