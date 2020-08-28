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

from hyperlink import (
    DecodedURL,
)

from treq.client import (
    HTTPClient,
)

import attr


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


def create_magic_folder_client(reactor, config):
    """
    Create a new MagicFolderClient instance that is speaking to the
    magic-folder defined by ``config``.

    :param GlobalConfigurationDatabase config: a Magic Folder global
        configuration

    :returns: a MagicFolderclient instance
    """
    def get_api_token():
        with config.api_token_path.open('rb') as f:
            return f.read()

    return MagicFolderClient(
        http_client=config.create_http_client(reactor),
        get_api_token=get_api_token,
    )
