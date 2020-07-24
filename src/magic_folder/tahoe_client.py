# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

import json

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from hyperlink import (
    DecodedURL,
)

from treq.client import (
    HTTPClient,
)

import attr


@attr.s
class TahoeClient(object):
    """
    An object that knows how to call a particular tahoe client's
    WebAPI. Usually this means a node-directory (to get the base URL)
    and a treq client (to make HTTP requests).
    """

    url = attr.ib(validator=attr.validators.instance_of(DecodedURL))
    http_client = attr.ib(validator=attr.validators.instance_of(HTTPClient))

    @inlineCallbacks
    def create_immutable_directory(self, directory_data):
        """
        Creates a new immutable directory in Tahoe.

        :param directory_data: a dict contain JSON-able data in a
            shape suitable for the `/uri?t=mkdir-immutable` Tahoe
            API. See
            https://tahoe-lafs.readthedocs.io/en/tahoe-lafs-1.12.1/frontends/webapi.html#creating-a-new-directory

        :returns: a capability-string
        """
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
        Creates a new immutable in Tahoe.

        :param producer: can take anything that treq's data= method to
            treq.request allows which is currently: str, file-like or
            IBodyProducer. See
            https://treq.readthedocs.io/en/release-20.3.0/api.html#treq.request

        :returns: a capability-string
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
        """
        Retrieve the raw data for a capability from Tahoe

        :param cap: a capability-string

        :returns: bytes
        """
        get_uri = self.url.replace(
            path=(u"uri",),
            query=[(u"uri", cap.decode("ascii"))],
        )
        res = yield self.http_client.get(get_uri.to_text())
        data = yield res.content()
        returnValue(data)

    @inlineCallbacks
    def stream_capability(self, cap, filelike):
        """
        Retrieve the raw data for a capability from Tahoe

        :param cap: a capability-string

        :param filelike: a writable file object. `.write` will be
            called on it an arbitrary number of times, but no other
            methods (that is, it won't be closed).

        :returns: Deferred that fires with `None`
        """
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

    :param url: the base URL of the Tahoe instance

    :param http_client: a Treq HTTP client

    :returns: a TahoeClient instance
    """

    client = TahoeClient(
        url=DecodedURL.from_text(url),
        http_client=http_client,
    )
    yield  # maybe we want to at least try getting / to see if it's alive?
    returnValue(client)
