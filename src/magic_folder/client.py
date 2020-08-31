# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

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


class CannotAccessApiError(ClientError):
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
    http_client = attr.ib(validator=attr.validators.instance_of(HTTPClient))
    get_api_token = attr.ib()

    def list_folders(self, include_secret_information=None):
        api_url = self.base_url.child(u'v1').child(u'magic-folder')
        if include_secret_information:
            api_url = api_url.replace(query=[(u"include_secret_information", u"1")])
        return self._make_token_authenticated_request(api_url)

    @inlineCallbacks
    def _make_token_authenticated_request(self, url):
        headers = {
            b"Authorization": u"Bearer {}".format(self.get_api_token()).encode("ascii"),
        }

        try:
            response = yield self.http_client.get(
                url.to_uri().to_text().encode('ascii'),
                headers=headers,
            )
        except ConnectError:
            raise CannotAccessApiError(
                "Can't reach the magic folder daemon at all"
            )

        body = yield _get_content_check_code([http.OK], response)
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
def create_testing_http_client(reactor, config, global_service, get_api_token):
    """
    :param global_service: an object providing the API of the global
        magic-folder service

    :param callable get_api_token: a no-argument callable that returns
        the current API token.

    :returns: a Treq HTTPClient which will do all requests to
        in-memory objects. These objects obtain their data from the
        service provided
    """
    v1_resource = APIv1(config, global_service)
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
