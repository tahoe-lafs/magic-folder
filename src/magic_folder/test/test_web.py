# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.status``.
"""


from __future__ import (
    unicode_literals,
)

from json import (
    dumps,
)

import attr

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
    one_of,
)

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    AfterPreprocessing,
    MatchesAny,
    IsInstance,
    Equals,
    raises,
    Always,
)
from testtools.twistedsupport import (
    failed,
    succeeded,
)

from twisted.python.failure import (
    Failure,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.http import (
    OK,
    CREATED,
    CONFLICT,
    UNAUTHORIZED,
    NOT_IMPLEMENTED,
    NOT_ALLOWED,
    BAD_REQUEST,
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
from treq.client import (
    HTTPClient,
)

from allmydata.uri import (
    from_string as cap_from_string,
)

from .common import (
    SyncTestCase,
    AsyncTestCase,
)
from .common_util import (
    loads_with_informative_error,
)
from .matchers import (
    matches_response,
    match_header,
)

from .strategies import (
    path_segments,
    folder_names,
    absolute_paths,
    absolute_paths_utf8,
    magic_folder_names,
    tahoe_lafs_dir_capabilities as dircaps,
    tahoe_lafs_chk_capabilities as chkcaps,
    tokens,
    filenodes,
    queued_items,
    not_json_binary,
)

from .fixtures import (
    NodeDirectory,
)

from .agentutil import (
    FailingAgent,
)
from ..cli import (
    MagicFolderServiceState,
)

from ..web import (
    magic_folder_resource,
    MagicFolderWebApi,
    status_for_item,
)

from ..status import (
    Status,
    status,
)

from ..common import (
    BadFolderName,
    BadResponseCode,
    BadDirectoryCapability,
    BadMetadataResponse,
)

def url_to_bytes(url):
    """
    Serialize a ``DecodedURL`` to an ASCII-only bytes string.  This result is
    suitable for use as an HTTP request path

    :param DecodedURL url: The URL to encode.

    :return bytes: The encoded URL.
    """
    return url.to_uri().to_text().encode("ascii")


class StatusTests(AsyncTestCase):
    """
    Tests for ``magic_folder.status.status``.
    """
    @given(folder_names(), absolute_paths_utf8().map(FilePath))
    def test_missing_node(self, folder_name, node_directory):
        """
        If the given node directory does not exist, ``status`` raises
        ``EnvironmentError``.
        """
        assume(not node_directory.exists())
        treq = object()
        with ExpectedException(IOError):
            status(folder_name, node_directory, treq),

    @given(folder_names())
    def test_missing_api_auth_token(self, folder_name):
        """
        If the given node directory does not contain an API authentication token,
        ``status`` raises ``EnvironmentError``.
        """
        node_directory = FilePath(self.mktemp())
        node_directory.makedirs()
        treq = object()
        self.assertThat(
            lambda: status(folder_name, node_directory, treq),
            raises(EnvironmentError),
        )

    @given(lists(
        folder_names(),
        unique=True,
        min_size=1,
        # Just keep the test from taking forever to run ...
        max_size=10,
    ), dircaps(), dircaps())
    def test_unknown_magic_folder_name(self, folder_names, collective_dircap, upload_dircap):
        """
        If a name which does not correspond to an existing magic folder is given,
        ``status`` raises ``BadFolderName``.
        """
        assume(collective_dircap != upload_dircap)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory))

        treq = object()
        nonexistent_folder_name = folder_names.pop()

        for folder_name in folder_names:
            node.create_magic_folder(
                folder_name,
                collective_dircap,
                upload_dircap,
                tempdir.child(u"folder"),
                60,
            )

        self.assertThat(
            lambda: status(nonexistent_folder_name, node_directory, treq),
            raises(BadFolderName),
        )

    @given(folder_names(), dircaps(), dircaps())
    def test_failed_node_connection(self, folder_name, collective_dircap, upload_dircap):
        """
        If an HTTP request to the Tahoe-LAFS node fails, ``status`` returns a
        ``Deferred`` that fails with that failure.
        """
        assume(collective_dircap != upload_dircap)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory))

        node.create_magic_folder(
            folder_name,
            collective_dircap,
            upload_dircap,
            tempdir.child(u"folder"),
            60,
        )

        exception = Exception("Made up failure")
        treq = HTTPClient(FailingAgent(Failure(exception)))
        self.assertThat(
            status(folder_name, node_directory, treq),
            failed(
                AfterPreprocessing(
                    lambda f: f.value,
                    Equals(exception),
                ),
            ),
        )

    @given(
        folder_names(),
        dircaps(),
        dircaps(),
        tokens(),
    )
    def test_cap_not_okay(self, folder_name, collective_dircap, upload_dircap, token):
        """
        If the response to a request for metadata about a capability for the magic
        folder does not receive an HTTP OK response, ``status`` fails with
        ``BadResponseCode``.
        """
        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory, token))

        node.create_magic_folder(
            folder_name,
            collective_dircap,
            upload_dircap,
            tempdir.child(u"folder"),
            60,
        )

        # A bare resource will result in 404s for all requests made.  That'll
        # do.
        treq = StubTreq(Resource())

        self.assertThat(
            status(folder_name, node_directory, treq),
            failed(
                AfterPreprocessing(
                    lambda f: f.value,
                    IsInstance(BadResponseCode),
                ),
            ),
        )

    @given(
        folder_names(),
        dircaps(),
        dircaps(),
        tokens(),
        tokens(),
    )
    def test_magic_folder_not_ok(self, folder_name, collective_dircap, upload_dircap, good_token, bad_token):
        """
        If the response to a request for magic folder status does not receive an
        HTTP OK response, ``status`` fails with ``BadResponseCode``.
        """
        assume(collective_dircap != upload_dircap)
        assume(good_token != bad_token)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory, good_token))

        node.create_magic_folder(
            folder_name,
            collective_dircap,
            upload_dircap,
            tempdir.child(u"folder"),
            60,
        )
        folders = {
            folder_name: StubMagicFolder(),
        }
        resource = magic_folder_uri_hierarchy(
            folders,
            collective_dircap,
            upload_dircap,
            {},
            {},
            bad_token,
        )
        treq = StubTreq(resource)
        self.assertThat(
            status(folder_name, node_directory, treq),
            failed(
                AfterPreprocessing(
                    lambda f: f.value,
                    IsInstance(BadResponseCode),
                ),
            ),
        )

    @given(
        folder_names(),
        dircaps(),
        # Not a directory cap at all!
        chkcaps(),
        tokens(),
        filenodes(),
    )
    def test_filenode_dmd(self, folder_name, collective_dircap, upload_dircap, token, filenode):
        """
        ``status`` fails with ``BadDirectoryCapability`` if the upload dircap does
        not refer to a directory object.
        """
        self._test_bad_dmd_metadata(
            folder_name,
            collective_dircap,
            upload_dircap,
            token,
            [u"filenode", filenode],
            BadDirectoryCapability,
        )

    @given(
        folder_names(),
        dircaps(),
        dircaps(),
        tokens(),
    )
    def test_unrecognizable_dmd(self, folder_name, collective_dircap, upload_dircap, token):
        """
        ``status`` fails with ``BadMetadataResponse`` if the upload dircap json
        metadata is not recognizable.
        """
        self._test_bad_dmd_metadata(
            folder_name,
            collective_dircap,
            upload_dircap,
            token,
            [u"filenode"],
            BadMetadataResponse,
        )

    @given(
        folder_names(),
        dircaps(),
        dircaps(),
        tokens(),
    )
    def test_error_dmd(self, folder_name, collective_dircap, upload_dircap, token):
        """
        ``status`` fails with ``BadMetadataResponse`` if the request for upload
        dircap json metadata returns an error dictionary.
        """
        self._test_bad_dmd_metadata(
            folder_name,
            collective_dircap,
            upload_dircap,
            token,
            {u"error": u"something went wrong"},
            BadMetadataResponse,
        )

    def _test_bad_dmd_metadata(
            self,
            folder_name,
            collective_dircap,
            upload_dircap,
            token,
            upload_json,
            exception_type,
    ):
        assume(collective_dircap != upload_dircap)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory, token))

        node.create_magic_folder(
            folder_name,
            collective_dircap,
            upload_dircap,
            tempdir.child(u"folder"),
            60,
        )
        folders = {
            folder_name: StubMagicFolder(),
        }

        treq = StubTreq(magic_folder_uri_hierarchy_from_magic_folder_json(
            folders,
            collective_dircap,
            dirnode_json(collective_dircap, {}),
            upload_dircap,
            upload_json,
            token,
        ))

        self.assertThat(
            status(folder_name, node_directory, treq),
            failed(
                AfterPreprocessing(
                    lambda f: f.value,
                    IsInstance(exception_type),
                ),
            ),
        )


    @given(
        folder_names(),
        dircaps(),
        dircaps(),
        tokens(),
        filenodes(),
        lists(queued_items()),
        lists(queued_items()),
    )
    def test_status(
            self,
            folder_name,
            collective_dircap,
            upload_dircap,
            token,
            local_file,
            upload_items,
            download_items,
    ):
        """
        ``status`` returns a ``Deferred`` that fires with a ``Status`` instance
        reflecting the status of the identified magic folder.
        """
        assume(collective_dircap != upload_dircap)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory, token))
        local_folder = tempdir.child(u"folder")
        local_folder.makedirs()
        node.create_magic_folder(
            folder_name,
            collective_dircap,
            upload_dircap,
            local_folder,
            60,
        )

        local_files = {
            u"foo": ["filenode", local_file],
        }
        remote_files = {
            u"participant-name": local_files,
        }
        folders = {
            folder_name: StubMagicFolder(
                uploader=StubQueue(upload_items),
                downloader=StubQueue(download_items),
            ),
        }
        treq = StubTreq(magic_folder_uri_hierarchy(
            folders,
            collective_dircap,
            upload_dircap,
            local_files,
            remote_files,
            token,
        ))
        self.assertThat(
            status(folder_name, node_directory, treq),
            succeeded(
                Equals(Status(
                    folder_name=folder_name,
                    local_files=local_files,
                    remote_files=remote_files,
                    folder_status=list(
                        status_for_item(kind, item)
                        for (kind, items) in [
                                ("upload", upload_items),
                                ("download", download_items),
                        ]
                        for item in items
                    ),
                )),
            ),
        )


def magic_folder_uri_hierarchy(
        folders,
        collective_dircap,
        upload_dircap,
        local_files,
        remote_files,
        token,
):
    upload_json = dirnode_json(
        upload_dircap,
        local_files,
    )
    collective_json = dirnode_json(
        collective_dircap, {
            key: dirnode_json(upload_dircap, {})
            for key
            in remote_files
        },
    )
    return magic_folder_uri_hierarchy_from_magic_folder_json(
        folders,
        collective_dircap,
        collective_json,
        upload_dircap,
        upload_json,
        token,
    )


def magic_folder_uri_hierarchy_from_magic_folder_json(
        folders,
        collective_dircap,
        collective_json,
        upload_dircap,
        upload_json,
        token,
):
    upload = Data(
        dumps(upload_json),
        b"text/plain",
    )
    collective = Data(
        dumps(collective_json),
        b"text/plain",
    )

    uri = Resource()
    uri.putChild(
        upload_dircap,
        upload,
    )
    uri.putChild(
        cap_from_string(upload_dircap.encode("ascii")).get_readonly().to_string(),
        upload,
    )
    uri.putChild(
        collective_dircap,
        collective,
    )

    state = MagicFolderServiceState()
    for (name, service) in folders.items():
        state.add_magic_folder(name, {}, service)

    api = MagicFolderWebApi(
        get_magic_folder=state.get_magic_folder,
        get_auth_token=lambda: token,
    )

    root = Resource()
    # Unfortunately the following two resource hierarchies should live at
    # different servers.  However, we lack multi-server support in our web
    # testing library.  So, they live side-by-side.  I hope that if the
    # implementation mistakenly sends requests to the wrong server it will be
    # blindingly obvious and this test shortcoming will not hurt us much.
    root.putChild(b"uri", uri)
    root.putChild(b"api", api)

    return root


def dirnode_json(cap_text, children):
    cap = cap_from_string(cap_text.encode("ascii"))
    info = {
        "verify_uri": cap.get_verify_cap().to_string(),
        "ro_uri": cap.get_readonly().to_string(),
        "children": children,
    }
    if cap.is_mutable():
        info["rw_uri"] = cap.to_string()
    return ["dirnode", info]


@attr.s
class StubQueue(object):
    items = attr.ib(default=attr.Factory(list))

    def get_status(self):
        for item in self.items:
            yield item


@attr.s
class StubMagicFolder(object):
    uploader = attr.ib(default=attr.Factory(StubQueue))
    downloader = attr.ib(default=attr.Factory(StubQueue))


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


MAGIC_FOLDER_URL = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

def authorized_request(treq, auth_token, method, url, body=b""):
    """
    Perform a request of the given url with the given client, request method,
    and authorization.

    :param treq: A ``treq``-module-alike.

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
    return treq.request(
        method,
        url_to_bytes(url),
        headers=headers,
        data=body,
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

    return treq_for_state(auth_token, state)


def treq_for_state(auth_token, state):
    """
    Construct a ``treq``-module-alike which is hooked up to a Magic Folder
    service using the given state.

    :param unicode auth_token: The authorization token accepted by the
        service.

    :param MagicFolderServiceState state: The underlying state to share with
        the Magic Folder service.

    :return: An object like the ``treq`` module.
    """
    root = magic_folder_resource(state, lambda: auth_token)
    return StubTreq(root)


def magic_folder_config_for_local_directory(local_directory):
    return {u"directory": local_directory}


class ListMagicFolderTests(SyncTestCase):
    """
    Tests for listing Magic Folders using **GET /v1/magic-folder** and
    ``V1MagicFolderAPI``.
    """
    @given(
        tokens(),
        sampled_from([b"PUT", b"PATCH", b"DELETE", b"OPTIONS"]),
    )
    def test_method_not_allowed(self, auth_token, method):
        """
        A request to **/v1/magic-folder** with a method other than **GET**
        receives a NOT ALLOWED or NOT IMPLEMENTED response.
        """
        treq = treq_for_folder_names(auth_token, [])
        self.assertThat(
            authorized_request(treq, auth_token, method, MAGIC_FOLDER_URL),
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
            authorized_request(treq, auth_token, b"GET", MAGIC_FOLDER_URL),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    headers_matcher=match_header(u"content-type", u"application/json"),
                    body_matcher=AfterPreprocessing(
                        loads_with_informative_error,
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


class CreateMagicFolderTests(SyncTestCase):
    """
    Tests for creating magic folders using **POST /v1/magic-folder** and
    ``V1MagicFolderAPI``.
    """
    @given(
        tokens(),
        one_of(
            dictionaries(text(), text()).map(dumps),
            not_json_binary(),
        )
    )
    def test_incorrect_magic_folder_definition(self, auth_token, request_body):
        """
        A **POST** to **/v1/magic-folder** that does not include a JSON-encoded
        request body sufficiently describing the new magic folder to create
        receives a **BAD REQUEST** response.
        """
        treq = treq_for_folder_names(auth_token, [])
        self.assertThat(
            authorized_request(
                treq,
                auth_token,
                b"POST",
                MAGIC_FOLDER_URL,
                request_body,
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                ),
            ),
        )

    @given(
        tokens(),
        magic_folder_names(),
        absolute_paths(),
    )
    def test_reject_duplicate_magic_folder(self, auth_token, folder_name, folder_location):
        """
        A **POST** to **/v1/magic-folder** with a request body containing a magic
        folder name which already exists receives a CONFLICT response.
        """
        state = MagicFolderServiceState()
        state.add_magic_folder(folder_name, None, None)
        treq = treq_for_state(auth_token, state)
        request_body = dumps({
            u"name": folder_name,
            u"local-path": folder_location,
        })
        self.assertThat(
            authorized_request(
                treq,
                auth_token,
                b"POST",
                MAGIC_FOLDER_URL,
                request_body,
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(CONFLICT),
                ),
            ),
        )

    @given(
        tokens(),
        magic_folder_names(),
        absolute_paths(),
    )
    def test_create_magic_folder(self, auth_token, folder_name, folder_location):
        """
        A **POST** to **/v1/magic-folder** with a request body containing a
        JSON-encoded object with **name** and **local-path** properties causes
        a new magic folder to be created with those properties.
        """
        state = MagicFolderServiceState()
        treq = treq_for_state(auth_token, state)
        request_body = dumps({
            u"name": folder_name,
            u"local-path": folder_location,
        })
        self.assertThat(
            authorized_request(
                treq,
                auth_token,
                b"POST",
                MAGIC_FOLDER_URL,
                request_body,
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(CREATED),
                    headers_matcher=match_header(u"content-type", u"application/json"),
                    body_matcher=AfterPreprocessing(
                        loads_with_informative_error,
                        Equals({
                            u"name": folder_name,
                            u"local-path": folder_location,
                        }),
                    ),
                ),
            ),
        )

        self.assertThat(
            state.get_magic_folder(folder_name),
            Always(),
        )
