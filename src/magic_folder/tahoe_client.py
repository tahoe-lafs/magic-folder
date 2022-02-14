# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

import json

from twisted.internet.defer import (
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
    Capability,
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


@attr.s(auto_exc=True)
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


@attr.s(auto_exc=True)
class InsufficientStorageServers(Exception):
    """
    Not enough storage-servers are currently connected.
    """
    connected = attr.ib()
    desired = attr.ib()

    def __str__(self):
        return "Wanted {} storage-servers but have {}".format(
            self.desired,
            self.connected,
        )


@attr.s(auto_exc=True)
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


@attr.s(auto_exc=True, str=False)
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


@inline_callbacks
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


@attr.s
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

    # When available and running, usually a ConnectedTahoeService will
    # update this based on the number of "connected" versus "desired"
    # servers. If this is None, we will not perform any operations at
    # all. If it is non-None we will raise the given error upon any
    # "mutable" operation (including "create a mutable")
    _error_on_mutable_operation = attr.ib(default=None)

    def mutables_okay(self):
        """
        It has been determined that it is currently okay to perform
        mutable operations.
        """
        self._error_on_mutable_operation = None

    def mutables_bad(self, err):
        """
        It has been determined that mutable operations are problemmatic
        :param Exception err: something suitable to 'raise'
        """
        self._error_on_mutable_operation = err

    @inline_callbacks
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
    @inline_callbacks
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
            u"POST",
            post_uri,
            data=json.dumps(directory_data).encode("utf8"),
        )
        capability_string = yield _get_content_check_code({OK, CREATED}, res)
        returnValue(Capability.from_string(capability_string.decode("utf8")))

    @exclusively
    @inline_callbacks
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
            u"PUT",
            put_uri,
            data=producer,
        )
        capability_string = yield _get_content_check_code({OK, CREATED}, res)
        returnValue(Capability.from_string(capability_string.decode("utf8")))

    @exclusively
    @inline_callbacks
    def create_mutable_directory(self):
        """
        Create a new mutable directory in Tahoe.

        :return Deferred[bytes]: The write capability string for the new
            directory.
        """
        if self._error_on_mutable_operation is not None:
            raise self._error_on_mutable_operation

        post_uri = self.url.child(u"uri").replace(
            query=[(u"t", u"mkdir")],
        )
        response = yield _request(
            self.http_client,
            u"POST",
            post_uri,
        )
        # Response code should probably be CREATED but it seems to be OK
        # instead.  Not sure if this is the real Tahoe-LAFS behavior or an
        # artifact of the test double.
        try:
            capability_string = yield _get_content_check_code({OK, CREATED}, response)
        except TahoeAPIError as e:
            raise CannotCreateDirectoryError(e)
        returnValue(Capability.from_string(capability_string.decode("utf8")))

    @inline_callbacks
    def list_directory(self, dir_cap):
        """
        List the contents of a read- or read/write- directory

        :param Capability dir_cap: the capability-string of the directory.
        """
        api_uri = self.url.child(
            u"uri",
            dir_cap.danger_real_capability_string(),
        ).add(
            u"t",
            u"json",
        ).to_uri().to_text().encode("ascii")
        action = start_action(
            action_type=u"magic-folder:cli:list-dir",
            # dirnode=dir_cap,
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
                Capability.from_string(json_metadata.get("rw_uri", json_metadata["ro_uri"])),
                json_metadata.get(u"metadata", {}),
            )
            for (name, (child_kind, json_metadata))
            in dirinfo[u"children"].items()
        })

    @inline_callbacks
    def directory_data(self, dir_cap):
        """
        Get the 'raw' directory data for a directory-capability. If you
        just want to list the entries, `list_directory` is better.

        :param Capability dir_cap: the capability-string of the directory.

        :returns dict: the JSON representing this directory
        """
        if not dir_cap.is_directory():
            raise ValueError(
                "{} is not a directory-capability".format(dir_cap)
            )
        api_uri = self.url.child(
            u"uri",
            dir_cap.danger_real_capability_string(),
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
    @inline_callbacks
    def add_entry_to_mutable_directory(self, mutable_cap, path_name, entry_cap, replace=False):
        """
        Adds an entry to a mutable directory

        :param Capability mutable_cap: a mutable to add an entry into

        :param unicode path_name: the name of the entry (i.e. the path
            segment)

        :param Capability entry_cap: the capability of the entry (could be
            any sort of capability).

        :param boolean replace: if set to True and if the entry already
            exists in the mutable directory, then change its contents.

        :return Deferred[None]: or exception on error
        """

        if self._error_on_mutable_operation is not None:
            raise self._error_on_mutable_operation

        if replace is True:
            replace_arg = u"true"
        elif replace is False:
            replace_arg = u"false"
        else:
            raise TypeError("replace value should be a boolean")

        post_uri = self.url.child(u"uri", mutable_cap.danger_real_capability_string(), path_name).replace(
            query=[
                (u"t", u"uri"),
                (u"replace", replace_arg),
            ],
        )
        response = yield _request(
            self.http_client,
            u"PUT",
            post_uri,
            data=entry_cap.danger_real_capability_string().encode("utf8"),
        )

        # Response code should probably be CREATED but it seems to be OK
        # instead.  Not sure if this is the real Tahoe-LAFS behavior or an
        # artifact of the test double.
        try:
            yield _get_content_check_code({OK, CREATED}, response)
        except TahoeAPIError as e:
            raise CannotAddDirectoryEntryError(
                entry_name=path_name,
                tahoe_error=e,
            )
        return

    @inline_callbacks
    def download_file(self, cap):
        """
        Retrieve the raw data for a capability from Tahoe. It is an error
        if the capability-string is a directory-capability, since the
        Tahoe `/uri` endpoint treats those specially.

        :param Capability cap: the content to download

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
        if not cap.is_file():
            raise ValueError(
                "{} is not a file capability".format(cap)
            )

        query_args = [(u"uri", cap.danger_real_capability_string())]

        get_uri = self.url.child(u"uri").replace(query=query_args)
        res = yield _request(
            self.http_client,
            u"GET",
            get_uri,
        )
        data = yield _get_content_check_code({OK}, res)
        returnValue(data)

    @inline_callbacks
    def stream_capability(self, cap, filelike):
        """
        Retrieve the raw data for a capability from Tahoe

        :param Capability cap: the content to stream

        :param filelike: a writable file object. `.write` will be
            called on it an arbitrary number of times, but no other
            methods (that is, it won't be closed).

        :returns: Deferred that fires with `None`
        """
        get_uri = self.url.child(u"uri").replace(
            query=[(u"uri", cap.danger_real_capability_string())],
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
