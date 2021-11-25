# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import json

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
    DeferredLock,
)
from twisted.web.http import (
    OK,
    CREATED,
)

from hyperlink import (
    DecodedURL,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    StubTreq,
)
from eliot import (
    start_action,
)
from eliot.twisted import (
    inline_callbacks,
)

import attr

from .util.capabilities import (
    is_directory_cap,
    is_file_cap,
)
from .util.twisted import (
    exclusively,
)

def _request(http_client, method, url, **kwargs):
    """
    Issue a request with the given parameters.

    :param HTTPClient http_client: The HTTP client to use.

    :param bytes method: The HTTP request method.

    :param DecodedURL url: The HTTP request path.

    :param **kwargs: Any additional keyword arguments to pass along to
        ``HTTPClient``.
    """
    return http_client.request(
        method,
        url.to_uri().to_text().encode("ascii"),
        **kwargs
    )


@attr.s(frozen=True)
class TahoeAPIError(Exception):
    """
    A Tahoe-LAFS HTTP API returned a failure code.
    """
    code = attr.ib()
    body = attr.ib()

    def __repr__(self):
        return "<TahoeAPIError code={} body={!r}>".format(
            self.code,
            self.body,
        )

    def __str__(self):
        return "Tahoe API error {}".format(self.code)


@attr.s(frozen=True)
class CannotCreateDirectoryError(Exception):
    """
    Failed to create a (mutable) directory.
    """
    tahoe_error = attr.ib()

    def __repr__(self):
        return "Could not create directory. Error code {}".format(
            self.tahoe_error.code,
        )

    def __str__(self):
        return repr(self)


@attr.s(frozen=True, str=False)
class CannotAddDirectoryEntryError(Exception):
    """
    Failed to add a sub-directory or file to a mutable directory.
    """
    entry_name = attr.ib()
    tahoe_error = attr.ib()

    def __str__(self):
        return "Couldn't add {} to directory. Error code {}".format(
            self.entry_name,
            self.tahoe_error.code,
        )


@inlineCallbacks
def _get_content_check_code(acceptable_codes, res):
    """
    Check that the given response's code is acceptable and read the response
    body.

    :raise TahoeAPIError: If the response code is not acceptable.

    :return Deferred[bytes]: If the response code is acceptable, a Deferred
        which fires with the response body.
    """
    body = yield res.content()
    if res.code not in acceptable_codes:
        raise TahoeAPIError(res.code, body)
    returnValue(body)


@attr.s(frozen=True)
class TahoeClient(object):
    """
    An object that knows how to call a particular tahoe client's
    WebAPI.

    :ivar DecodedURL url: The root of the Tahoe-LAFS client node's HTTP API.

    :ivar HTTPClient http_client: The client to use to make HTTP requests.
    """

    url = attr.ib(validator=attr.validators.instance_of(DecodedURL))
    _lock = attr.ib(init=False, factory=DeferredLock)

    # treq should provide an interface but it doesn't ...  HTTPClient and
    # StubTreq are both the same kind of thing.  HTTPClient is the one that
    # does real networking, StubTreq is the one that operates in-memory on a
    # local resource object.
    http_client = attr.ib(
        validator=attr.validators.instance_of((HTTPClient, StubTreq)),
    )

    @inlineCallbacks
    def get_welcome(self):
        """
        Fetch the JSON 'welcome page' from Tahoe

        :returns: bytes
        """
        resp = yield self.http_client.get(
            self.url.add(u"t", u"json").to_uri().to_text().encode("ascii"),
        )
        returnValue((yield resp.json()))

    @exclusively
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
        post_uri = self.url.child(u"uri").replace(
            query=[(u"t", u"mkdir-immutable")],
        )
        res = yield _request(
            self.http_client,
            b"POST",
            post_uri,
            data=json.dumps(directory_data),
        )
        capability_string = yield _get_content_check_code({OK, CREATED}, res)
        returnValue(capability_string)

    @exclusively
    @inlineCallbacks
    def create_immutable(self, producer):
        """
        Creates a new immutable in Tahoe.

        :param producer: can take anything that treq's data= method to
            treq.request allows which is currently: str, file-like or
            IBodyProducer. See
            https://treq.readthedocs.io/en/release-20.3.0/api.html#treq.request

        :return Deferred[bytes]: A Deferred which fires with the capability
            string for the new immutable object.
        """
        put_uri = self.url.child(u"uri")
        res = yield _request(
            self.http_client,
            b"PUT",
            put_uri,
            data=producer,
        )
        capability_string = yield _get_content_check_code({OK, CREATED}, res)
        returnValue(capability_string)

    @exclusively
    @inlineCallbacks
    def create_mutable_directory(self):
        """
        Create a new mutable directory in Tahoe.

        :return Deferred[bytes]: The write capability string for the new
            directory.
        """
        post_uri = self.url.child(u"uri").replace(
            query=[(u"t", u"mkdir")],
        )
        response = yield _request(
            self.http_client,
            b"POST",
            post_uri,
        )
        # Response code should probably be CREATED but it seems to be OK
        # instead.  Not sure if this is the real Tahoe-LAFS behavior or an
        # artifact of the test double.
        try:
            capability_string = yield _get_content_check_code({OK, CREATED}, response)
        except TahoeAPIError as e:
            raise CannotCreateDirectoryError(e)
        returnValue(capability_string)

    @inline_callbacks
    def list_directory(self, dir_cap):
        """
        List the contents of a read- or read/write- directory

        :param bytes dir_cap: the capability-string of the directory.
        """
        api_uri = self.url.child(
            u"uri",
            dir_cap.decode("ascii"),
        ).add(
            u"t",
            u"json",
        ).to_uri().to_text().encode("ascii")
        action = start_action(
            action_type=u"magic-folder:cli:list-dir",
            # leaks secrets: dirnode_uri=dir_cap.decode("ascii"),
            # leaks secrets: api_uri=api_uri,
        )
        with action.context():
            response = yield self.http_client.get(
                api_uri,
            )
            if response.code != 200:
                content = yield response.content()
                raise TahoeAPIError(response.code, content)

            kind, dirinfo = yield response.json()

            if kind != u"dirnode":
                raise ValueError("Capability is a '{}' not a 'dirnode'".format(kind))

            action.add_success_fields(
                children=dirinfo[u"children"],
            )

        returnValue({
            name: (
                json_metadata.get("rw_uri", json_metadata["ro_uri"]).encode("ascii"),
                json_metadata.get(u"metadata", {}),
            )
            for (name, (child_kind, json_metadata))
            in dirinfo[u"children"].items()
        })

    @inlineCallbacks
    def directory_data(self, dir_cap):
        """
        Get the 'raw' directory data for a directory-capability. If you
        just want to list the entries, `list_directory` is better.

        :param bytes dir_cap: the capability-string of the directory.

        :returns dict: the JSON representing this directory
        """
        if not is_directory_cap(dir_cap):
            raise ValueError(
                "{} is not a directory-capability".format(dir_cap)
            )
        api_uri = self.url.child(
            u"uri",
            dir_cap.decode("ascii"),
        ).add(
            u"t",
            u"json",
        ).to_uri().to_text().encode("ascii")

        response = yield self.http_client.get(
            api_uri,
        )
        if response.code != 200:
            content = yield response.content()
            raise TahoeAPIError(response.code, content)

        _, dirinfo = yield response.json()
        returnValue(dirinfo)

    @exclusively
    @inlineCallbacks
    def add_entry_to_mutable_directory(self, mutable_cap, path_name, entry_cap, replace=False):
        """
        Adds an entry to a mutable directory

        :param bytes mutable_cap: the capability-string of a mutable
            to add an entry into

        :param unicode path_name: the name of the entry (i.e. the path
            segment)

        :param bytes entry_cap: the capability of the entry (could be
            any sort of capability).

        :param boolean replace: if set to True and if the entry already
            exists in the mutable directory, then change its contents.

        :return Deferred[None]: or exception on error
        """

        if replace is True:
            replace_arg = u"true"
        elif replace is False:
            replace_arg = u"false"
        else:
            raise TypeError("replace value should be a boolean")

        post_uri = self.url.child(u"uri", mutable_cap.decode("utf8"), path_name).replace(
            query=[
                (u"t", u"uri"),
                (u"replace", replace_arg),
            ],
        )
        response = yield _request(
            self.http_client,
            b"PUT",
            post_uri,
            data=entry_cap,
        )
        # Response code should probably be CREATED but it seems to be OK
        # instead.  Not sure if this is the real Tahoe-LAFS behavior or an
        # artifact of the test double.
        try:
            capability_string = yield _get_content_check_code({OK, CREATED}, response)
        except TahoeAPIError as e:
            raise CannotAddDirectoryEntryError(
                entry_name=path_name,
                tahoe_error=e,
            )
        returnValue(capability_string)

    @inlineCallbacks
    def download_file(self, cap):
        """
        Retrieve the raw data for a capability from Tahoe. It is an error
        if the capability-string is a directory-capability, since the
        Tahoe `/uri` endpoint treats those specially.

        :param cap: a capability-string

        :returns: bytes
        """
        # Visiting /uri with a directory-capability causes Tahoe to
        # render an HTML page .. instead adding ?t=json tells it to
        # send the JSON instead. We can't just use ?t=json all the
        # time though, because for non-directories it returns the
        # metadata, not the data. So, the caller needs to know if they
        # have a dir-cap or not and call list_directory() or
        # directory_data() to get "raw" representation

        # we further insist that this is "a file capability" because
        # the API says "_file" in it
        if not is_file_cap(cap):
            raise ValueError(
                "{} is not a file capability".format(cap)
            )

        query_args = [(u"uri", cap.decode("ascii"))]

        get_uri = self.url.child(u"uri").replace(query=query_args)
        res = yield _request(
            self.http_client,
            b"GET",
            get_uri,
        )
        data = yield _get_content_check_code({OK}, res)
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
        get_uri = self.url.child(u"uri").replace(
            query=[(u"uri", cap.decode("ascii"))],
        )
        res = yield self.http_client.get(get_uri.to_text())
        if res.code != OK:
            raise TahoeAPIError(res.code, None)
        yield res.collect(filelike.write)


def create_tahoe_client(url, http_client):
    """
    Create a new TahoeClient instance that is speaking to a particular
    Tahoe node.

    :param DecodedURL url: the base URL of the Tahoe instance

    :param http_client: a Treq HTTP client

    :returns: a TahoeClient instance
    """
    return TahoeClient(
        url=url,
        http_client=http_client,
    )
