# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

from __future__ import (
    absolute_import,
    division,
    print_function,
)

import json

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.internet.endpoints import (
    clientFromString,
)
from twisted.internet.error import (
    ConnectError,
)

from twisted.python.filepath import FilePath
from twisted.web import (
    http,
)
from twisted.web.client import (
    Agent,
)
from twisted.web.iweb import (
    IAgentEndpointFactory,
)

from hyperlink import (
    DecodedURL,
)

from treq.client import (
    HTTPClient,
)
from treq.testing import (
    RequestTraversalAgent,
    StubTreq,
)
from zope.interface import (
    implementer,
)

import attr

from .web import (
    APIv1,
    magic_folder_resource,
)
from .testing.web import (
    _SynchronousProducer,
)


class ClientError(Exception):
    """
    Base class for all exceptions in this module
    """


class CannotAccessAPIError(ClientError):
    """
    The Magic Folder HTTP API can't be reached at all
    """


@attr.s(frozen=True)
class MagicFolderApiError(ClientError):
    """
    A Magic Folder HTTP API returned a failure code.
    """
    code = attr.ib()
    body = attr.ib()

    def __repr__(self):
        return "<MagicFolderApiError code={} body={!r}>".format(
            self.code,
            self.body,
        )

    def __str__(self):
        return u"Magic Folder HTTP API reported error {}: {}".format(
            self.code,
            self.body,
        )


@inlineCallbacks
def _get_content_check_code(acceptable_codes, res):
    """
    Check that the given response's code is acceptable and read the response
    body.

    :raise MagicFolderApiError: If the response code is not acceptable.

    :return Deferred[bytes]: If the response code is acceptable, a Deferred
        which fires with the response body.
    """
    body = yield res.content()
    if res.code not in acceptable_codes:
        raise MagicFolderApiError(res.code, body)
    returnValue(body)


@attr.s
class MagicFolderClient(object):
    """
    An object that knows how to call a particular Magic Folder HTTP API.

    :ivar HTTPClient http_client: The client to use to make HTTP requests.

    :ivar callable get_api_token: returns the current API token
    """

    # we only use the path-part not the domain
    base_url = DecodedURL.from_text(u"http://invalid./")
    http_client = attr.ib(validator=attr.validators.instance_of((HTTPClient, StubTreq)))
    get_api_token = attr.ib()

    def list_folders(self, include_secret_information=None):
        api_url = self.base_url.child(u'v1').child(u'magic-folder')
        if include_secret_information:
            api_url = api_url.replace(query=[(u"include_secret_information", u"1")])
        return self._authorized_request("GET", api_url)

    def add_snapshot(self, magic_folder, path):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'snapshot')
        api_url = api_url.set(u'path', path)
        return self._authorized_request("POST", api_url)

    def add_participant(self, magic_folder, author_name, personal_dmd):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'participants')
        body = json.dumps({
            "author": {
                "name": author_name,
                # not yet
                # "public_key_base32": author_verify_key,
            },
            "personal_dmd": personal_dmd,
        })
        return self._authorized_request("POST", api_url, body=body.encode("utf8"))

    def list_participants(self, magic_folder):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'participants')
        return self._authorized_request("GET", api_url)

    def add_folder(self, magic_folder, author_name, local_path, poll_interval, scan_interval):
        # type: (unicode, unicode, FilePath, int, int) -> dict
        api_url = self.base_url.child(u'v1').child(u'magic-folder')
        return self._authorized_request("POST", api_url, body=json.dumps({
            'name': magic_folder,
            'author_name': author_name,
            'local_path': local_path.path,
            'poll_interval': poll_interval,
            'scan_interval': scan_interval,
        }, ensure_ascii=False).encode('utf-8'))

    @inlineCallbacks
    def _authorized_request(self, method, url, body=b""):
        """
        :param str method: GET, POST etc http verb

        :param DecodedURL url: the url to request
        """
        try:
            response = yield authorized_request(
                self.http_client,
                self.get_api_token(),
                method,
                url,
                body=body,
            )

        except ConnectError:
            raise CannotAccessAPIError(
                "Can't reach the magic folder daemon at all"
            )

        body = yield _get_content_check_code([http.OK, http.CREATED], response)
        # all responses should contain JSON
        returnValue(json.loads(body))


@implementer(IAgentEndpointFactory)
@attr.s
class _StaticEndpointFactory(object):
    """
    Return the same endpoint for every request. This is the endpoint
    factory used by `create_http_client`.

    :ivar endpoint: the endpoint returned for every request
    """

    endpoint = attr.ib()

    def endpointForURI(self, uri):
        return self.endpoint


def create_http_client(reactor, api_client_endpoint_str):
    """
    :param reactor: Twisted reactor

    :param unicode api_client_endpoint_str: a Twisted client endpoint-string

    :returns: a Treq HTTPClient which will do all requests to the
        indicated endpoint
    """
    return HTTPClient(
        agent=Agent.usingEndpointFactory(
            reactor,
            _StaticEndpointFactory(
                clientFromString(reactor, api_client_endpoint_str),
            ),
        ),
    )


# See https://github.com/LeastAuthority/magic-folder/issues/280
# global_service should expect/demand an Interface
def create_testing_http_client(reactor, config, global_service, get_api_token, tahoe_client, status_service):
    """
    :param global_service: an object providing the API of the global
        magic-folder service

    :param callable get_api_token: a no-argument callable that returns
        the current API token.

    :param IStatus status_service: a status service to use

    :returns: a Treq HTTPClient which will do all requests to
        in-memory objects. These objects obtain their data from the
        service provided
    """
    v1_resource = APIv1(config, global_service, status_service, tahoe_client).app.resource()
    root = magic_folder_resource(get_api_token, v1_resource)
    client = HTTPClient(
        agent=RequestTraversalAgent(root),
        data_to_body_producer=_SynchronousProducer,
    )
    return client


def create_magic_folder_client(reactor, config, http_client):
    """
    Create a new MagicFolderClient instance that is speaking to the
    magic-folder defined by ``config``.

    :param GlobalConfigurationDatabase config: a Magic Folder global
        configuration

    :param treq.HTTPClient http_client: the client used to make all
        requests.

    :returns: a MagicFolderclient instance
    """
    def get_api_token():
        return config.api_token

    return MagicFolderClient(
        http_client=http_client,
        get_api_token=get_api_token,
    )


def url_to_bytes(url):
    """
    Serialize a ``DecodedURL`` to an ASCII-only bytes string.  This result is
    suitable for use as an HTTP request path

    :param DecodedURL url: The URL to encode.

    :return bytes: The encoded URL.
    """
    return url.to_uri().to_text().encode("ascii")


def authorized_request(http_client, auth_token, method, url, body=b""):
    """
    Perform a request of the given url with the given client, request method,
    and authorization.

    :param http_client: A treq.HTTPClient instance

    :param unicode auth_token: The Magic Folder authorization token to
        present.

    :param bytes method: The HTTP request method to use.

    :param DecodedURL url: The request URL.

    :param bytes body: The request body to include.

    :return: Whatever ``treq.request`` returns.
    """
    headers = {
        b"Authorization": u"Bearer {}".format(auth_token).encode("ascii"),
    }
    return http_client.request(
        method,
        url_to_bytes(url),
        headers=headers,
        data=body,
    )
