# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

import json

from eliot.twisted import (
    inline_callbacks,
)

from twisted.internet.defer import (
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
    _create_v1_resource,
    _create_experimental_resource,
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


@attr.s(auto_exc=True)
class MagicFolderApiError(ClientError):
    """
    A Magic Folder HTTP API returned a failure code.
    """
    code = attr.ib()
    reason = attr.ib()
    body = attr.ib()

    @property
    def reason(self):
        return self.body.get("reason")

    def __repr__(self):
        return "<MagicFolderApiError code={} reason={!r} body={!r}>".format(
            self.code,
            self.reason,
            self.body,
        )

    def __str__(self):
        extra_fields = {k: v for (k, v) in self.body.items() if k != "reason"}
        return u"Magic Folder HTTP API reported error {}: {}{}".format(
            self.code,
            self.reason,
            " ({})".format(extra_fields) if extra_fields else "",
        )


@inline_callbacks
def _get_json_check_code(acceptable_codes, res):
    """
    Check that the given response's code is acceptable and read the response
    body.

    :raise MagicFolderApiError: If the response code is not acceptable.

    :return Deferred[Any]: If the response code is acceptable, a Deferred
        which fires with the parsed response body.
    """
    body = yield res.json()
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

    def recent_changes(self, folder_name, number=None):
        api_url = self.base_url.child(u'v1').child(u'magic-folder').child(folder_name).child("recent-changes")
        if number is not None:
            api_url = api_url.replace(query=[(u"number", str(number))])
        return self._authorized_request("GET", api_url)

    def add_snapshot(self, magic_folder, relpath):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'snapshot')
        api_url = api_url.set(u'path', relpath)
        return self._authorized_request("POST", api_url)

    def file_status(self, magic_folder):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'file-status')
        return self._authorized_request("GET", api_url)

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

    def list_conflicts(self, magic_folder):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'conflicts')
        return self._authorized_request("GET", api_url)

    def tahoe_objects(self, magic_folder):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'tahoe-objects')
        return self._authorized_request("GET", api_url)

    def add_folder(self, magic_folder, author_name, local_path, poll_interval, scan_interval):
        # type: (str, str, FilePath, int, int) -> dict
        api_url = self.base_url.child(u'v1').child(u'magic-folder')
        return self._authorized_request("POST", api_url, body=json.dumps({
            'name': magic_folder,
            'author_name': author_name,
            'local_path': local_path.path,
            'poll_interval': poll_interval,
            'scan_interval': scan_interval,
        }, ensure_ascii=False).encode('utf-8'))

    def scan_folder_local(self, magic_folder):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'scan-local')
        return self._authorized_request("PUT", api_url, body=b"")

    def poll_folder_remote(self, magic_folder):
        api_url = self.base_url.child(u'v1', u'magic-folder', magic_folder, u'poll-remote')
        return self._authorized_request("PUT", api_url, body=b"")

    def leave_folder(self, magic_folder, really_delete_write_capability):
        # type: (str, bool) -> dict
        api_url = self.base_url.child(u"v1").child(u"magic-folder").child(magic_folder)
        return self._authorized_request(
            "DELETE",
            api_url,
            body=json.dumps(
                {
                    "really-delete-write-capability": really_delete_write_capability,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def invite(self, magic_folder, participant_name, mode):
        # type: (str, str) -> dict
        api_url = self.base_url.child(u"experimental").child(u"magic-folder").child(magic_folder).child(u"invite")
        return self._authorized_request(
            "POST",
            api_url,
            body=json.dumps(
                {
                    "participant-name": participant_name,
                    "mode": mode,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def invite_wait(self, magic_folder, invite_id):
        # type: (str, str) -> dict
        api_url = self.base_url.child(u"experimental").child(u"magic-folder").child(magic_folder).child(u"invite-wait")
        return self._authorized_request(
            "POST",
            api_url,
            body=json.dumps(
                {
                    "id": invite_id,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def join(self, magic_folder, invite_code, local_dir, author, poll_interval, scan_interval):
        api_url = self.base_url.child(u"experimental").child(u"magic-folder").child(magic_folder).child(u"join")
        return self._authorized_request(
            "POST",
            api_url,
            body=json.dumps(
                {
                    "invite-code": invite_code,
                    "local-directory": local_dir.asTextMode().path,
                    "author": author,
                    "poll-interval": poll_interval,
                    "scan-interval": scan_interval,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def list_invites(self, magic_folder):
        # type: (str, ) -> dict
        api_url = self.base_url.child(u"experimental").child(u"magic-folder").child(magic_folder).child(u"invites")
        return self._authorized_request(
            "GET",
            api_url,
        )

    def cancel_invite(self, magic_folder, invite_id):
        # type: (str, str) -> dict
        api_url = self.base_url.child(u"experimental").child(u"magic-folder").child(magic_folder).child(u"invite-cancel")
        return self._authorized_request(
            "POST",
            api_url,
            body=json.dumps(
                {
                    "id": invite_id,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def enable_feature(self, feature):
        """
        Call the HTTP API to mark a given feature on. Error if it is
        already on.
        """
        api_url = self.base_url.child(u"v1", u"config", u"enable-feature", feature)
        return self._authorized_request("POST", api_url)

    def disable_feature(self, feature):
        """
        Call the HTTP API to mark a given feature off. Error if it is
        already off.
        """
        api_url = self.base_url.child(u"v1", u"config", u"disable-feature", feature)
        return self._authorized_request("POST", api_url)

    @inline_callbacks
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

        body = yield _get_json_check_code([http.OK, http.CREATED], response)
        returnValue(body)


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

    :param str api_client_endpoint_str: a Twisted client endpoint-string

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
def create_testing_http_client(reactor, config, global_service, get_api_token, status_service):
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
    v1_resource = _create_v1_resource(config, global_service, status_service)
    exp_resource = _create_experimental_resource(config, global_service)
    root = magic_folder_resource(get_api_token, v1_resource, exp_resource)
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

    :param bytes auth_token: The Magic Folder authorization token to
        present.

    :param bytes method: The HTTP request method to use.

    :param DecodedURL url: The request URL.

    :param bytes body: The request body to include.

    :return: Whatever ``treq.request`` returns.
    """
    headers = {
        b"Authorization": b"Bearer " + auth_token,
    }
    return http_client.request(
        method,
        url,
        headers=headers,
        data=body,
    )
