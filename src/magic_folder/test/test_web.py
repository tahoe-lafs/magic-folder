# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.web``.
"""


from __future__ import (
    unicode_literals,
)

from json import (
    loads,
)

from hyperlink import (
    DecodedURL,
)

from hypothesis import (
    given,
    assume,
)

from hypothesis.strategies import (
    lists,
    text,
    binary,
    dictionaries,
    sampled_from,
)

from testtools.matchers import (
    AfterPreprocessing,
    MatchesAny,
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)

from twisted.web.http import (
    OK,
    UNAUTHORIZED,
    NOT_IMPLEMENTED,
    NOT_ALLOWED,
)
from twisted.web.resource import (
    Resource,
)
from twisted.web.static import (
    Data,
)

from treq.testing import (
    StubTreq,
)

from .common import (
    SyncTestCase,
)
from .matchers import (
    matches_response,
    header_contains,
)

from .strategies import (
    path_segments,
    folder_names,
    absolute_paths,
    tokens,
)

from ..cli import (
    MagicFolderServiceState,
)

from ..web import (
    magic_folder_resource,
)

def url_to_bytes(url):
    """
    Serialize a ``DecodedURL`` to an ASCII-only bytes string.  This result is
    suitable for use as an HTTP request path

    :param DecodedURL url: The URL to encode.

    :return bytes: The encoded URL.
    """
    return url.to_uri().to_text().encode("ascii")


class AuthorizationTests(SyncTestCase):
    """
    Tests for the authorization requirements for resources beneath ``/v1``.
    """
    @given(
        good_token=tokens(),
        bad_tokens=lists(tokens()),
        child_segments=lists(text()),
    )
    def test_unauthorized(self, good_token, bad_tokens, child_segments):
        """
        If the correct bearer token is not given in the **Authorization** header
        of the request then the response code is UNAUTHORIZED.

        :param bytes good_token: A bearer token which, when presented, should
            authorize access to the resource.

        :param bad_tokens: A list of bearer token which, when presented all at
            once, should not authorize access to the resource.  If this is
            empty no tokens are presented at all.  If it contains more than
            one element then it creates a bad request with multiple
            authorization header values.

        :param [unicode] child_segments: Additional path segments to add to the
            request path beneath **v1**.
        """
        # We're trying to test the *unauthorized* case.  Don't randomly hit
        # the authorized case by mistake.
        assume([good_token] != bad_tokens)

        def get_auth_token():
            return good_token

        root = magic_folder_resource(MagicFolderServiceState(), get_auth_token)
        treq = StubTreq(root)
        url = DecodedURL.from_text(u"http://example.invalid./v1").child(*child_segments)
        encoded_url = url_to_bytes(url)

        # A request with no token at all or the wrong token should receive an
        # unauthorized response.
        headers = {}
        if bad_tokens:
            headers[b"Authorization"] = list(
                u"Bearer {}".format(bad_token).encode("ascii")
                for bad_token
                in bad_tokens
            )

        self.assertThat(
            treq.get(
                encoded_url,
                headers=headers,
            ),
            succeeded(
                matches_response(code_matcher=Equals(UNAUTHORIZED)),
            ),
        )

    @given(
        auth_token=tokens(),
        child_segments=lists(path_segments()),
        content=binary(),
    )
    def test_authorized(self, auth_token, child_segments, content):
        """
        If the correct bearer token is not given in the **Authorization** header
        of the request then the response code is UNAUTHORIZED.

        :param bytes auth_token: A bearer token which, when presented, should
            authorize access to the resource.

        :param [unicode] child_segments: Additional path segments to add to the
            request path beneath **v1**.

        :param bytes content: The bytes we expect to see on a successful
            request.
        """
        def get_auth_token():
            return auth_token

        # Since we don't want to exercise any real magic-folder application
        # logic we'll just magic up the child resource being requested.
        branch = Data(
            content,
            b"application/binary",
        )
        segments_remaining = child_segments[:]
        while segments_remaining:
            name = segments_remaining.pop()
            resource = Resource()
            resource.putChild(name.encode("utf-8"), branch)
            branch = resource

        root = magic_folder_resource(
            MagicFolderServiceState(),
            get_auth_token,
            _v1_resource=branch,
        )

        treq = StubTreq(root)
        url = DecodedURL.from_text(u"http://example.invalid./v1").child(*child_segments)
        encoded_url = url_to_bytes(url)

        # A request with no token at all or the wrong token should receive an
        # unauthorized response.
        headers = {
            b"Authorization": u"Bearer {}".format(auth_token).encode("ascii"),
        }

        self.assertThat(
            treq.get(
                encoded_url,
                headers=headers,
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    body_matcher=Equals(content),
                ),
            ),
        )


def authorized_request(treq, auth_token, method, url):
    """
    Perform a request of the given url with the given client, request method,
    and authorization.

    :param treq: A ``treq``-module-alike.

    :param unicode auth_token: The Magic Folder authorization token to
        present.

    :param bytes method: The HTTP request method to use.

    :param bytes url: The request URL.

    :return: Whatever ``treq.request`` returns.
    """
    headers = {
        b"Authorization": u"Bearer {}".format(auth_token).encode("ascii"),
    }
    return treq.request(
        method,
        url,
        headers=headers,
    )


def treq_for_folder_names(auth_token, names):
    """
    Construct a ``treq``-module-alike which is hooked up to a Magic Folder
    service with Magic Folders of the given names.

    :param unicode auth_token: The authorization token accepted by the
        service.

    :param [unicode] names: The names of the Magic Folders which will exist.

    :return: An object like the ``treq`` module.
    """
    return treq_for_folders(auth_token, dict.fromkeys(names, {u"directory": None}))


def treq_for_folders(auth_token, folders):
    """
    Construct a ``treq``-module-alike which is hooked up to a Magic Folder
    service with Magic Folders like the ones given.

    :param unicode auth_token: The authorization token accepted by the
        service.

    :param folders: A mapping from Magic Folder names to their configurations.
        These are the folders which will appear to exist.

    :return: An object like the ``treq`` module.
    """
    state = MagicFolderServiceState()
    for name, config in folders.items():
        state.add_magic_folder(name, config, object())

    root = magic_folder_resource(state, lambda: auth_token)
    return StubTreq(root)


def magic_folder_config_for_local_directory(local_directory):
    return {u"directory": local_directory}


class ListMagicFolderTests(SyncTestCase):
    """
    Tests for listing Magic Folders using **GET /v1/magic-folder** and
    ``V1MagicFolderAPI``.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")
    encoded_url = url_to_bytes(url)

    @given(
        tokens(),
        sampled_from([b"PUT", b"POST", b"PATCH", b"DELETE", b"OPTIONS"]),
    )
    def test_method_not_allowed(self, auth_token, method):
        """
        A request to **/v1/magic-folder** with a method other than **GET**
        receives a NOT ALLOWED or NOT IMPLEMENTED response.
        """
        treq = treq_for_folder_names(auth_token, [])
        self.assertThat(
            authorized_request(treq, auth_token, method, self.encoded_url),
            succeeded(
                matches_response(
                    code_matcher=MatchesAny(
                        Equals(NOT_ALLOWED),
                        Equals(NOT_IMPLEMENTED),
                    ),
                ),
            ),
        )

    @given(
        tokens(),
        dictionaries(
            folder_names(),
            absolute_paths(),
        ),
    )
    def test_list_folders(self, auth_token, folders):
        """
        A request for **GET /v1/magic-folder** receives a response that is a
        JSON-encoded list of Magic Folders.

        :param dict[unicode, unicode] folders: A mapping from folder names to
            local filesystem paths where we shall pretend the local filesystem
            state for those folders resides.
        """
        treq = treq_for_folders(
            auth_token, {
                name: magic_folder_config_for_local_directory(path)
                for (name, path)
                in folders.items()
            },
        )

        self.assertThat(
            authorized_request(treq, auth_token, b"GET", self.encoded_url),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    headers_matcher=header_contains({
                        u"Content-Type": Equals([u"application/json"]),
                    }),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({
                            u"folders": list(
                                {u"name": name, u"local-path": path}
                                for (name, path)
                                in sorted(folders.items())
                            ),
                        }),
                    )
                ),
            ),
        )
