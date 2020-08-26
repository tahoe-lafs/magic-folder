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
    MatchesDict,
    MatchesListwise,
)
from testtools.twistedsupport import (
    succeeded,
    has_no_result,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.web.http import (
    OK,
    CREATED,
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

from allmydata.util.base32 import (
    b2a,
)

from .common import (
    SyncTestCase,
)
from .matchers import (
    matches_response,
    header_contains,
    is_hex_uuid,
)

from .strategies import (
    path_segments,
    folder_names,
    relative_paths,
    tokens,
)

from ..cli import (
    MagicFolderService,
)
from ..web import (
    APIv1,
    magic_folder_resource,
)
from ..config import (
    create_global_configuration,
)
from ..snapshot import (
    create_local_author,
)
from .strategies import (
    local_authors,
)

def url_to_bytes(url):
    """
    Serialize a ``DecodedURL`` to an ASCII-only bytes string.  This result is
    suitable for use as an HTTP request path

    :param DecodedURL url: The URL to encode.

    :return bytes: The encoded URL.
    """
    return url.to_uri().to_text().encode("ascii")


# Pick any single API token value.  Any test suite that is not specifically
# for authorization can use this because don't need Hypothesis to
# comprehensively explore the authorization token input space in those tests.
AUTH_TOKEN = b"0" * 16


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

        root = magic_folder_resource(get_auth_token, Resource())
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
            get_auth_token,
            v1_resource=branch,
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


def treq_for_folders(reactor, basedir, auth_token, folders, start_folder_services):
    """
    Construct a ``treq``-module-alike which is hooked up to a Magic Folder
    service with Magic Folders like the ones given.

    :param FilePath basedir: A non-existant directory to create and populate
        with a new Magic Folder service configuration.

    :param unicode auth_token: The authorization token accepted by the
        service.

    :param folders: A mapping from Magic Folder names to their configurations.
        These are the folders which will appear to exist.

    :param bool start_folder_services: If ``True``, start the Magic Folder
        service objects.  Otherwise, don't.

    :return: An object like the ``treq`` module.
    """
    global_config = create_global_configuration(
        basedir,
        # Make this endpoint string and the one below parse but make them
        # invalid, too, because we don't want anything to start listening on
        # these during this set of tests.
        u"tcp:-1",
        # It wants to know where the Tahoe-LAFS node directory is but we don't
        # have one and we don't want to invoke any functionality that requires
        # one.  Give it something bogus.
        FilePath(u"/non-tahoe-directory"),
        u"tcp:-1",
    )
    for name, config in folders.items():
        global_config.create_magic_folder(
            name,
            config[u"magic-path"],
            config[u"state-path"],
            config[u"author"],
            config[u"collective-dircap"],
            config[u"upload-dircap"],
            config[u"poll-interval"],
        )

    global_service = MagicFolderService(
        reactor,
        global_config,
        # Pass in *something* to override the default TahoeClient so that it
        # doesn't try to look up a Tahoe-LAFS node URL in the non-existent
        # directory we supplied above.
        object(),
    )

    if start_folder_services:
        # Reach in and start the individual service for the folder we're going
        # to interact with.  This is required for certain functionality, eg
        # snapshot creation.  We avoid starting the whole global_service
        # because it wants to do error-prone things like bind ports.
        for name in folders:
            global_service.get_folder_service(name).startService()

    v1_resource = APIv1(global_config, global_service)
    root = magic_folder_resource(lambda: auth_token, v1_resource)
    return StubTreq(root)


def magic_folder_config(author, state_path, local_directory):
    return {
        u"magic-path": local_directory,
        u"state-path": state_path,
        u"author": author,
        u"collective-dircap": u"URI:DIR2-RO:{}:{}".format(b2a("\0" * 16), b2a("\1" * 32)),
        u"upload-dircap": u"URI:DIR2:{}:{}".format(b2a("\2" * 16), b2a("\3" * 32)),
        u"poll-interval": 60,
    }


class ListMagicFolderTests(SyncTestCase):
    """
    Tests for listing Magic Folders using **GET /v1/magic-folder** and
    ``V1MagicFolderAPI``.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")
    encoded_url = url_to_bytes(url)

    def setUp(self):
        super(ListMagicFolderTests, self).setUp()
        self.author = create_local_author(u"alice")

    @given(
        sampled_from([b"PUT", b"POST", b"PATCH", b"DELETE", b"OPTIONS"]),
    )
    def test_method_not_allowed(self, method):
        """
        A request to **/v1/magic-folder** with a method other than **GET**
        receives a NOT ALLOWED or NOT IMPLEMENTED response.
        """
        treq = treq_for_folders(object(), FilePath(self.mktemp()), AUTH_TOKEN, {}, False)
        self.assertThat(
            authorized_request(treq, AUTH_TOKEN, method, self.encoded_url),
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
        dictionaries(
            folder_names(),
            # We need absolute paths but at least we can make them beneath the
            # test working directory.
            relative_paths().map(FilePath),
        ),
    )
    def test_list_folders(self, folders):
        """
        A request for **GET /v1/magic-folder** receives a response that is a
        JSON-encoded list of Magic Folders.

        :param dict[unicode, unicode] folders: A mapping from folder names to
            local filesystem paths where we shall pretend the local filesystem
            state for those folders resides.
        """
        for path_u in folders.values():
            # Fix it so non-ASCII works reliably. :/ This is fine here but we
            # leave the original as text mode because that works better with
            # the config/database APIs.
            path_b = path_u.asBytesMode("utf-8")
            path_b.makedirs(ignoreExistingDirectory=True)

        treq = treq_for_folders(
            object(),
            FilePath(self.mktemp()),
            AUTH_TOKEN, {
                name: magic_folder_config(self.author, FilePath(self.mktemp()), path_u)
                for (name, path_u)
                in folders.items()
            },
            False,
        )

        self.assertThat(
            authorized_request(treq, AUTH_TOKEN, b"GET", self.encoded_url),
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
                                {u"name": name, u"local-path": path_u.path}
                                for (name, path_u)
                                in sorted(folders.items())
                            ),
                        }),
                    )
                ),
            ),
        )


class CreateSnapshotTests(SyncTestCase):
    """
    Tests for creating a new snapshot in an existing Magic Folder using a
    **POST**.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/snapshot")

    @given(
        local_authors(),
        folder_names(),
        relative_paths(),
        binary(),
    )
    def test_wait_for_completion(self, author, folder_name, path_in_folder, some_content):
        """
        A **POST** request to **/v1/snapshot/:folder-name** does not receive a
        response before the snapshot has been created in the local database.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        some_file = local_path.preauthChild(path_in_folder).asBytesMode("utf-8")
        some_file.parent().makedirs(ignoreExistingDirectory=True)
        some_file.setContent(some_content)

        treq = treq_for_folders(
            object(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, FilePath(self.mktemp()), local_path)},
            False,
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                url_to_bytes(self.url.child(folder_name).set(u"path", path_in_folder)),
            ),
            has_no_result(),
        )


    @given(
        local_authors(),
        folder_names(),
        relative_paths(),
        binary(),
    )
    def test_create_snapshot(self, author, folder_name, path_in_folder, some_content):
        """
        A **POST** to **/v1/snapshot/:folder-name** with a **path** query argument
        creates a new local snapshot for the file at the given path in the
        named folder.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        some_file = local_path.preauthChild(path_in_folder).asBytesMode("utf-8")
        some_file.parent().makedirs(ignoreExistingDirectory=True)
        some_file.setContent(some_content)

        from twisted.internet import reactor
        treq = treq_for_folders(
            reactor,
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, FilePath(self.mktemp()), local_path)},
            True,
        )
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                url_to_bytes(self.url.child(folder_name).set(u"path", path_in_folder)),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(CREATED),
                ),
            ),
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"GET",
                url_to_bytes(self.url),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    headers_matcher=header_contains({
                        u"Content-Type": Equals([u"application/json"]),
                    }),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict({
                            folder_name: MatchesDict({
                                path_in_folder: MatchesListwise([
                                    MatchesDict({
                                        u"type": Equals(u"local"),
                                        u"identifier": is_hex_uuid(),
                                        # XXX It would be nice to see some
                                        # parents if there are any.
                                        u"parents": Equals([]),
                                        u"content-path": AfterPreprocessing(
                                            lambda path: FilePath(path).getContent(),
                                            Equals(some_content),
                                        ),
                                        u"author": Equals(author.to_remote_author().to_json()),
                                    }),
                                ]),
                            }),
                        }),
                    ),
                ),
            ),
        )
