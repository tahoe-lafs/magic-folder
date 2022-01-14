# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2020 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Test-helpers for clients that use the WebUI.

NOTE: This code should be in upstream Tahoe-LAFS.  None of it exists in
1.14.0.  Some of it has been pushed upstream and will make it into 1.15.0
without further efforts but other parts have not.  Changes here should always
be pushed upstream eventually but not so quickly that we have to submit a PR
to Tahoe-LAFS every few days.
"""

import json
import time

import hashlib

import attr

from hyperlink import DecodedURL

from twisted.web.resource import (
    Resource,
)
from twisted.web.iweb import (
    IBodyProducer,
)
from twisted.web import (
    http,
)

from twisted.internet.defer import (
    succeed,
)

from ..util.encoding import normalize

from treq.client import (
    HTTPClient,
    FileBodyProducer,
)
from treq.testing import (
    RequestTraversalAgent,
)
from zope.interface import implementer

import allmydata.uri
from allmydata.interfaces import (
    IDirnodeURI,
)
from allmydata.util import (
    base32,
)


__all__ = (
    "create_fake_tahoe_root",
    "create_tahoe_treq_client",
)


class _FakeTahoeRoot(Resource, object):
    """
    An in-memory 'fake' of a Tahoe WebUI root. Currently it only
    implements (some of) the `/uri` resource and a static welcome page
    """

    def __init__(self, uri=None):
        """
        :param uri: a Resource to handle the `/uri` tree.
        """
        Resource.__init__(self)  # this is an old-style class :(
        self._uri = uri
        self._welcome = _FakeTahoeWelcome()
        self.putChild(b"uri", self._uri)
        self.putChild(b"", self._welcome)

    def add_data(self, kind, data):
        return self._uri.add_data(kind, data)

    def add_mutable_data(self, kind, data):
        # Adding mutable data always makes a new object.
        return self._uri.add_mutable_data(kind, data)

    def fail_next_directory_update(self):
        """
        Cause the next mutable directory-entry update to fail.
        """
        self._uri._put_errors.append(object())


class _FakeTahoeWelcome(Resource, object):
    """
    Welcome page. This only renders the ?t=json case.
    """
    isLeaf = True

    def render_GET(self, request):
        """
        Normally the "welcome" / front page. We only need to support the
        ?t=json for tests.
        """
        assert b"t" in request.args, "must pass ?t= query argument"
        assert request.args[b"t"][0] == b"json", "must pass ?t=json query argument"
        return json.dumps({
            "introducers": {
                "statuses": ["Fake test status"]
            },
            "servers": [
                {
                    "connection_status": "Connected to localhost:-1 via tcp",
                    "nodeid": "v0-ehyafwjjwck2x2rsmwhsojcynfdzzn66xxyso5ev2joyughw47dq",
                    "last_received_data": time.time(),
                    "version": "tahoe-lafs/1.15.1",
                    "available_space": 1234567890,
                    "nickname": "node0",
                },
            ]
        }).encode("utf8")


KNOWN_CAPABILITIES = [
    getattr(allmydata.uri, t).BASE_STRING.decode("ascii")
    for t in dir(allmydata.uri)
    if hasattr(getattr(allmydata.uri, t), 'BASE_STRING')
]
MUTABLE_CAPABILITIES = [
    u'URI:DIR2:',
    u'URI:DIR2-RO:',
    u'URI:SSK:',
    u'URI:SSK-RO:',
    u'URI:MDMF:',
    u'URI:MDMF-RO:',
]

def capability_generator(kind):
    """
    Deterministically generates a stream of valid capabilities of the
    given kind. The N, K and size values aren't related to anything
    real.

    :param str kind: the kind of capability, like `URI:CHK`

    :returns: a generator that yields new capablities of a particular
        kind.
    """
    if kind not in KNOWN_CAPABILITIES:
        raise ValueError(
            "Unknown capability kind '{} (valid are {})'".format(
                kind,
                ", ".join(KNOWN_CAPABILITIES),
            )
        )
    # what we do here is to start with empty hashers for the key and
    # ueb_hash and repeatedly feed() them a zero byte on each
    # iteration .. so the same sequence of capabilities will always be
    # produced. We could add a seed= argument if we wanted to produce
    # different sequences.
    number = 0
    key_hasher = hashlib.new("sha256")
    ueb_hasher = hashlib.new("sha256")  # ueb means "URI Extension Block"

    # capabilities are "prefix:<128-bits-base32>:<256-bits-base32>:N:K:size"
    while True:
        number += 1
        key_hasher.update("\x00".encode("utf8"))
        ueb_hasher.update("\x00".encode("utf8"))

        key = base32.b2a(key_hasher.digest()[:16]).decode("ascii")  # key is 16 bytes
        ueb_hash = base32.b2a(ueb_hasher.digest()).decode("ascii")  # ueb hash is 32 bytes

        cap = u"{kind}{key}:{ueb_hash}:{n}:{k}:{size}".format(
            kind=kind,
            key=key,
            ueb_hash=ueb_hash,
            n=1,
            k=1,
            size=number * 1000,
        )
        yield cap


def _get_node_format(cap):
    """
    Determine a plausible data format for the object references by the given
    capability.

    Some capabilities have multiple possible data formats behind them.  We
    don't have enough deep knowledge of Tahoe-LAFS data structures or enough
    state represented in this fake to actually have *any* data format (we just
    have strings in memory).  But make something up to satisfy the interface.

    :param IURI cap: The capability to consider.

    :return unicode: A string describing a *possible* data format for the
        object referenced by the given capability.
    """
    if cap.is_mutable():
        return u"SDMF"
    return u"CHK"


@attr.s
class _FakeTahoeUriHandler(Resource, object):
    """
    An in-memory fake of (some of) the `/uri` endpoint of a Tahoe
    WebUI
    """

    isLeaf = True

    data = attr.ib(default=attr.Factory(dict))
    capability_generators = attr.ib(default=attr.Factory(dict))

    # allow tests to cause failures
    _put_errors = attr.ib(default=attr.Factory(list))

    def _generate_capability(self, kind):
        """
        :param str kind: any valid capability-string type

        :returns: the next capability-string for the given kind
        """
        if kind not in self.capability_generators:
            self.capability_generators[kind] = capability_generator(kind)
        capability = next(self.capability_generators[kind])
        if kind.startswith("URI:DIR2") and not kind.startswith("URI:DIR2-CHK"):
            # directory-capabilities don't have the trailing size etc
            # information unless they're immutable ...
            parts = capability.split(":")
            capability = ":".join(parts[:4])
        return capability

    def _add_new_data(self, kind, data):
        """
        Add brand new data to the store.

        :param bytes kind: The kind of capability, represented as the static
            string prefix on the resulting capability string (eg "URI:DIR2:").

        :param data: The data.  The type varies depending on ``kind``.

        :return bytes: The capability-string for the data.
        """
        cap = self._generate_capability(kind)
        # it should be impossible for this to already be in our data,
        # but check anyway to be sure
        if cap in self.data:
            raise Exception("Internal error; key already exists somehow")
        self.data[cap] = data
        return cap

    def add_data(self, kind, data):
        """
        Add some immutable data to our grid.

        If the data exists already, an existing capability is returned.
        Otherwise, a new capability is returned.

        :return (bool, bytes): The first element is True if the data is
            freshly added.  The second element is the capability-string for
            the data.
        """
        if not isinstance(data, bytes):
            raise TypeError("'data' must be bytes")

        # for immutables we need to check content
        if kind not in MUTABLE_CAPABILITIES:
            for k in self.data:
                if self.data[k] == data:
                    return (False, k)

        return (True, self._add_new_data(kind, data))

    def add_mutable_data(self, kind, data):
        """
        Add some mutable data to our grid.

        :return bytes: The capability-string for the data.
        """
        if not isinstance(data, bytes):
            raise TypeError("'data' must be bytes")
        return (False, self._add_new_data(kind, data))

    def render_PUT(self, request):
        uri = DecodedURL.from_text(request.uri.decode("utf8"))
        fmt = "chk"
        for arg, value in uri.query:
            if arg == "format":
                fmt = value.lower()
        if fmt != "chk":
            raise NotImplementedError()

        if len(request.postpath):
            return self._add_entry_to_dir(
                request=request,
                dircap=request.postpath[0],
                segments=request.postpath[1:],
            )

        data = request.content.read()
        fresh, cap = self.add_data("URI:CHK:", data)
        if fresh:
            request.setResponseCode(http.CREATED)  # real code does this for brand-new files
        else:
            request.setResponseCode(http.OK)  # replaced/modified files
        return cap.encode("utf8")

    def _add_entry_to_dir(self, request, dircap, segments):
        """
        Adds an entry to a mutable directory. Only handles a single-level
        deep.
        """
        if self._put_errors:
            self._put_errors.pop(0)
            return None

        if len(segments) != 1:
            raise Exception(
                "Need exactly one path segment (got {})".format(len(segments))
            )
        path = normalize(segments[0].decode("utf8"))
        dircap = request.postpath[0].decode("utf8")
        if not dircap.startswith("URI:DIR2"):
            raise Exception(
                "Can't add entry to non-mutable directory '{}'".format(dircap)
            )
        try:
            dir_raw_data = self.data[dircap]
        except KeyError:
            raise Exception(
                "No directory for '{}'".format(dircap)
            )

        content_cap = request.content.read().decode("utf8")
        content = allmydata.uri.from_string(content_cap)

        kind = "dirnode" if IDirnodeURI.providedBy(content) else "filenode"

        # Objects can be mutable or immutable.  Capabilities can be read-only
        # or write-only.  Don't mix up mutability with writeability.  You
        # might have a read-only cap for a mutable object.  In this case,
        # someone *else* might be able to change the object even though you
        # can't.
        metadata = {
            "mutable": content.is_mutable(),
            "ro_uri": content_cap,
            "verify_uri": content.get_verify_cap().to_string().decode("ascii"),
            "format": _get_node_format(content),
        }
        if content_cap != content.get_readonly().to_string().decode("utf8"):
            metadata["rw_uri"] = content_cap

        if kind == "filenode" and content.get_size() is not None:
            metadata["size"] = content.get_size()

        dir_data = json.loads(dir_raw_data)
        if path in dir_data[1]["children"]:
            replace = request.args.get(b"replace", [b""])[0].lower() in (b"true", b"1", b"on")
            if not replace:
                request.setResponseCode(http.BAD_REQUEST)
                return b""

        dir_data[1]["children"][segments[0].decode("utf8")] = [kind, metadata]
        self.data[dircap] = json.dumps(dir_data).encode("utf8")
        return b""

    def _mkdir_data_to_internal(self, raw_data):
        """
        Transforms the data that Tahoe-LAFS's ?t=mkdir-immutable (and
        ?t=mkdir) API takes into the form that it'll spit out again
        from the JSON directory-listing API. The incoming JSON data is
        essentially just the children of the new directory.
        """
        # incoming from a client is essentially just the "children"
        # part. Internally in Tahoe-LAFS these are represented as a
        # series of net-strings but are returned from the GET API
        # shaped like the below:
        data = {} if not raw_data else json.loads(raw_data)
        return json.dumps([
            "dirnode",
            {
                "children": data,
            }
        ]).encode("utf8")

    def _add_immutable_directory(self, raw_data):
        return self.add_data(
            "URI:DIR2-CHK:",
            self._mkdir_data_to_internal(raw_data),
        )

    def _add_mutable_directory(self, raw_data):
        return self.add_mutable_data(
            "URI:DIR2:",
            self._mkdir_data_to_internal(raw_data),
        )

    def render_POST(self, request):
        t = request.args[b"t"][0]
        data = request.content.read()

        type_to_handler = {
            b"mkdir-immutable": self._add_immutable_directory,
            b"mkdir": self._add_mutable_directory,
        }
        handler = type_to_handler[t]
        fresh, cap = handler(data)
        return cap.encode("utf8")

    def render_GET(self, request):
        uri = DecodedURL.from_text(request.uri.decode('utf8'))
        capability = None
        for arg, value in uri.query:
            if arg == u"uri":
                capability = value
        # it's legal to use the form "/uri/<capability>"
        if capability is None and request.postpath and request.postpath[0]:
            capability = request.postpath[0].decode("utf8")

        # Tahoe lets you get the children of directory-nodes by
        # appending names after the capability; we support up to 1
        # such path
        if len(request.postpath) > 1:
            if len(request.postpath) > 2:
                raise NotImplementedError
            child_name = request.postpath[1].decode("utf8")
            return self._get_child_of_directory(request, capability, child_name)

        # if we don't yet have a capability, that's an error
        if capability is None:
            request.setResponseCode(http.BAD_REQUEST)
            return b"GET /uri requires uri="

        # the user gave us a capability; if our Grid doesn't have any
        # data for it, that's an error.
        if capability not in self.data:
            # Tahoe-LAFS actually has several different behaviors for the
            # ostensible "not found" case.
            #
            # * A request for a CHK cap will receive a GONE response with
            #   "NoSharesError" (and some other text) in a text/plain body.
            # * A request for a DIR2 cap will receive an OK response with
            #   a huge text/html body including "UnrecoverableFileError".
            # * A request for the child of a DIR2 cap will receive a GONE
            #   response with "UnrecoverableFileError" (and some other text)
            #   in a text/plain body.
            #
            # Also, all of these are *actually* behind a redirect to
            # /uri/<CAP>.
            #
            # GONE makes the most sense here and I don't want to deal with
            # redirects so here we go.
            request.setResponseCode(http.GONE)
            return u"No data for '{}'".format(capability).encode("ascii")

        return self.data[capability]

    def _get_child_of_directory(self, request, capability, child_name):
        """
        Return the data which is a in a child of a directory.

        :param bytes capability: the directory-capability

        :param unicode child_name: the name of the child
        """
        raw_data = self.data[capability]
        if not raw_data:
            raise Exception(
                u"No child '{}' in empty directory".format(child_name)
            )
        dir_data = json.loads(raw_data)
        try:
            child_cap = dir_data[1]["children"][child_name][1]["ro_uri"]
            child_data = self.data[child_cap]
        except KeyError:
            request.setResponseCode(http.GONE)
            return b"Child not found"
        return child_data


def create_fake_tahoe_root():
    """
    If you wish to pre-populate data into the fake Tahoe grid, retain
    a reference to this root by creating it yourself and passing it to
    `create_tahoe_treq_client`. For example::

        root = create_fake_tahoe_root()
        cap_string = root.add_data(...)
        client = create_tahoe_treq_client(root)

    :returns: an IResource instance that will handle certain Tahoe URI
        endpoints similar to a real Tahoe server.
    """
    root = _FakeTahoeRoot(
        uri=_FakeTahoeUriHandler(),
    )
    return root


@implementer(IBodyProducer)
class _SynchronousProducer(object):
    """
    A partial implementation of an :obj:`IBodyProducer` which produces its
    entire payload immediately.  There is no way to access to an instance of
    this object from :obj:`RequestTraversalAgent` or :obj:`StubTreq`, or even a
    :obj:`Resource: passed to :obj:`StubTreq`.

    This does not implement the :func:`IBodyProducer.stopProducing` method,
    because that is very difficult to trigger.  (The request from
    `RequestTraversalAgent` would have to be canceled while it is still in the
    transmitting state), and the intent is to use `RequestTraversalAgent` to
    make synchronous requests.
    """

    def __init__(self, body):
        """
        Create a synchronous producer with some bytes.
        """
        if isinstance(body, FileBodyProducer):
            body = body._inputFile.read()

        if not isinstance(body, bytes):
            raise ValueError(
                "'body' must be bytes not '{}'".format(type(body))
            )
        self.body = body
        self.length = len(body)

    def startProducing(self, consumer):
        """
        Immediately produce all data.
        """
        consumer.write(self.body)
        return succeed(None)


def create_tahoe_treq_client(root=None):
    """
    :param root: an instance created via `create_fake_tahoe_root`. The
        caller might want a copy of this to call `.add_data` for example.

    :returns: an instance of treq.client.HTTPClient wired up to
        in-memory fakes of the Tahoe WebUI. Only a subset of the real
        WebUI is available.
    """

    if root is None:
        root = create_fake_tahoe_root()

    client = HTTPClient(
        agent=RequestTraversalAgent(root),
        data_to_body_producer=_SynchronousProducer,
    )
    return client
