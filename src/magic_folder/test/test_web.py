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
    dumps,
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
    ContainsDict,
    IsInstance,
)
from testtools.twistedsupport import (
    succeeded,
    has_no_result,
)

from twisted.web.http import (
    OK,
    CREATED,
    UNAUTHORIZED,
    NOT_IMPLEMENTED,
    NOT_ALLOWED,
    NOT_ACCEPTABLE,
    NOT_FOUND,
    BAD_REQUEST,
    INTERNAL_SERVER_ERROR,
)
from twisted.internet.task import Clock
from twisted.web.resource import (
    Resource,
)
from twisted.web.static import (
    Data,
)
from twisted.python.filepath import (
    FilePath,
)

from nacl.encoding import (
    Base32Encoder,
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
    author_names,
)

from ..snapshot import (
    create_local_author,
    format_filenode,
)
from ..cli import (
    MagicFolderService,
)
from ..web import (
    magic_folder_resource,
)
from ..config import (
    create_global_configuration,
    load_global_configuration,
)
from ..client import (
    create_testing_http_client,
    authorized_request,
    url_to_bytes,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from ..tahoe_client import (
    create_tahoe_client,
)
from .strategies import (
    local_authors,
    tahoe_lafs_readonly_dir_capabilities,
    tahoe_lafs_dir_capabilities,
    tahoe_lafs_chk_capabilities,
)
from ..util.capabilities import (
    to_readonly_capability,
)

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


def treq_for_folders(reactor, basedir, auth_token, folders, start_folder_services,
                     tahoe_client=None):
    """
    Construct a ``treq``-module-alike which is hooked up to a Magic Folder
    service with Magic Folders like the ones given.

    :param reactor: A reactor to give to the ``MagicFolderService`` which will
        back the HTTP interface.

    :param FilePath basedir: A non-existant directory to create and populate
        with a new Magic Folder service configuration.

    :param unicode auth_token: The authorization token accepted by the
        service.

    :param folders: A mapping from Magic Folder names to their configurations.
        These are the folders which will appear to exist.

    :param bool start_folder_services: If ``True``, start the Magic Folder
        service objects.  Otherwise, don't.

    :param TahoeClient tahoe_client: if provided, used as the
        tahoe-client. If it is not provided, an 'empty' Tahoe client is
        provided (which is likely to cause errors if any Tahoe endpoitns
        are called via this test).

    :return: An object like the ``treq`` module.
    """
    global_config = create_global_configuration(
        basedir,
        # Make this endpoint string and the one below parse but make them
        # invalid, too, because we don't want anything to start listening on
        # these during this set of tests.
        #
        # https://github.com/LeastAuthority/magic-folder/issues/276
        u"tcp:-1",
        # It wants to know where the Tahoe-LAFS node directory is but we don't
        # have one and we don't want to invoke any functionality that requires
        # one.  Give it something bogus.
        FilePath(u"/non-tahoe-directory"),
        u"tcp:127.0.0.1:-1",
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

    if tahoe_client is None:
        # the caller must provide a properly-set-up Tahoe client if
        # they care about Tahoe responses. Since they didn't, an
        # "empty" one is sufficient.
        tahoe_client = create_tahoe_client(DecodedURL.from_text(u""), StubTreq(Resource()))
    global_service = MagicFolderService(
        reactor,
        global_config,
        # Provide a TahoeClient so MagicFolderService doesn't try to look up a
        # Tahoe-LAFS node URL in the non-existent directory we supplied above
        # in its efforts to create one itself.
        tahoe_client,
    )

    if start_folder_services:
        # Reach in and start the individual service for the folder we're going
        # to interact with.  This is required for certain functionality, eg
        # snapshot creation.  We avoid starting the whole global_service
        # because it wants to do error-prone things like bind ports.
        for name in folders:
            global_service.get_folder_service(name).startService()

    return create_testing_http_client(reactor, global_config, global_service, lambda: auth_token, tahoe_client)


def magic_folder_config(author, state_path, local_directory):
    # see also treq_for_folders() where these dicts are turned into
    # real magic-folder configs
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
            authorized_request(treq, AUTH_TOKEN, method, self.url),
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

        basedir = FilePath(self.mktemp())
        treq = treq_for_folders(
            object(),
            basedir,
            AUTH_TOKEN,
            {
                name: magic_folder_config(self.author, FilePath(self.mktemp()), path_u)
                for (name, path_u)
                in folders.items()
            },
            False,
        )

        # note that treq_for_folders() will end up creating a configuration here
        config = load_global_configuration(basedir)
        expected_folders = {
            name: config.get_magic_folder(name)
            for name in config.list_magic_folders()
        }

        self.assertThat(
            authorized_request(treq, AUTH_TOKEN, b"GET", self.url),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    headers_matcher=header_contains({
                        u"Content-Type": Equals([u"application/json"]),
                    }),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({
                            name: {
                                u"name": name,
                                u"author": {
                                    u"name": config.author.name,
                                    u"verify_key": config.author.verify_key.encode(Base32Encoder),
                                },
                                u"magic_path": config.magic_path.path,
                                u"stash_path": config.stash_path.path,
                                u"poll_interval": config.poll_interval,
                                u"is_admin": config.is_admin(),
                            }
                            for name, config
                            in expected_folders.items()
                        }),
                    ),
                ),
            ),
        )


class CreateSnapshotTests(SyncTestCase):
    """
    Tests for creating a new snapshot in an existing Magic Folder using a
    **POST**.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

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
            # The interesting behavior of this test hinges on this flag.  We
            # decline to start the folder services here.  Therefore, no local
            # snapshots will ever be created.  This lets us observe the
            # request in a state where it is waiting to receive its response.
            # This demonstrates that the response is not delivered before the
            # local snapshot is created.  See test_create_snapshot for the
            # alternative case.
            start_folder_services=False,
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "snapshot").set(u"path", path_in_folder),
            ),
            has_no_result(),
        )

    @given(
        local_authors(),
        folder_names(),
        relative_paths(),
    )
    def test_create_fails(self, author, folder_name, path_in_folder):
        """
        If a local snapshot cannot be created, a **POST** to
        **/v1/snapshot/<folder-name>** receives a response with an HTTP error
        code.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        # You may not create a snapshot of a directory.
        not_a_file = local_path.preauthChild(path_in_folder).asBytesMode("utf-8")
        not_a_file.makedirs(ignoreExistingDirectory=True)

        treq = treq_for_folders(
            object(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, FilePath(self.mktemp()), local_path)},
            # This test carefully targets a failure mode that doesn't require
            # the service to be running.
            start_folder_services=False,
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "snapshot").set(u"path", path_in_folder),
            ),
            succeeded(
                matches_response(
                    # Maybe this could be BAD_REQUEST instead, sometimes, if
                    # the path argument was bogus somehow.
                    code_matcher=Equals(INTERNAL_SERVER_ERROR),
                    headers_matcher=header_contains({
                        u"Content-Type": Equals([u"application/json"]),
                    }),
                    body_matcher=AfterPreprocessing(
                        loads,
                        ContainsDict({
                            u"reason": IsInstance(unicode),
                        }),
                    ),
                ),
            ),
        )

    @given(
        local_authors(),
        folder_names(),
        relative_paths(),
        binary(),
    )
    def test_create_snapshot(self, author, folder_name, path_in_folder, some_content):
        """
        A **POST** to **/v1/magic-folder/:folder-name/snapshot** with a **path** query argument
        creates a new local snapshot for the file at the given path in the
        named folder.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        some_file = local_path.preauthChild(path_in_folder).asBytesMode("utf-8")
        some_file.parent().makedirs(ignoreExistingDirectory=True)
        some_file.setContent(some_content)

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, FilePath(self.mktemp()), local_path)},
            # Unlike test_wait_for_completion above we start the folder
            # services.  This will allow the local snapshot to be created and
            # our request to receive a response.
            start_folder_services=True,
        )
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "snapshot").set(u"path", path_in_folder),
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
                DecodedURL.from_text(u"http://example.invalid./v1/snapshots")
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

    @given(
        local_authors(),
        folder_names(),
        sampled_from([u"..", u"foo/../..", u"/tmp/foo"]),
        binary(),
    )
    def test_create_snapshot_fails(self, author, folder_name, path_outside_folder, some_content):
        """
        A **POST** to **/v1/snapshot/:folder-name** with a **path** query argument
        fails if the **path** is outside the magic-folder
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, FilePath(self.mktemp()), local_path)},
            # Unlike test_wait_for_completion above we start the folder
            # services.  This will allow the local snapshot to be created and
            # our request to receive a response.
            start_folder_services=True,
        )
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "snapshot").set(u"path", path_outside_folder),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(NOT_ACCEPTABLE),
                ),
            ),
        )


class ParticipantsTests(SyncTestCase):
    """
    Tests relating to the '/v1/participants/<folder>` API
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    def test_participants_no_folder(self):
        """
        An error results using /v1/participants API on non-existent
        folder.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        root = create_fake_tahoe_root()
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(root),
        )
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {},
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"GET",
                self.url.child("a-folder-that-doesnt-exist", "participants"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(NOT_FOUND),
                ),
            )
        )

    @given(
        folder_names(),
        tahoe_lafs_readonly_dir_capabilities(),
    )
    def test_add_participant(self, folder_name, personal_dmd):
        """
        Adding a new participant works.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        folder_config = magic_folder_config(
            create_local_author("iris"),
            FilePath(self.mktemp()),
            local_path,
        )
        # we can't add a new participant if their DMD is the same as
        # one we already have .. and because Hypothesis is 'sneaky' we
        # have to make sure it's not our collective, either
        assume(personal_dmd != folder_config["upload-dircap"])
        assume(personal_dmd != to_readonly_capability(folder_config["upload-dircap"]))
        assume(personal_dmd != folder_config["collective-dircap"])
        assume(personal_dmd != to_readonly_capability(folder_config["collective-dircap"]))

        root = create_fake_tahoe_root()
        # put our Collective DMD into the fake root
        root._uri.data[folder_config["collective-dircap"]] = dumps([
            u"dirnode",
            {
                u"children": {
                    "iris": format_filenode(folder_config["upload-dircap"]),
                },
            },
        ])
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(root),
        )
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        # add a participant using the API
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd,
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(CREATED),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({})
                    )
                )
            )
        )

        # confirm that the "list participants" API includes the added
        # participant
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"GET",
                self.url.child(folder_name, "participants"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({
                            u"iris": {
                                u"personal_dmd": folder_config["upload-dircap"],
                            },
                            u'kelly': {
                                u'personal_dmd': personal_dmd,
                            }
                        })
                    )
                )
            )
        )

    @given(
        author_names(),
        folder_names(),
    )
    def test_add_participant_wrong_json(self, author, folder_name):
        """
        Missing keys in 'participant' JSON produces error
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        folder_config = magic_folder_config(
            create_local_author(author),
            FilePath(self.mktemp()),
            local_path,
        )

        root = create_fake_tahoe_root()
        # put our Collective DMD into the fake root
        root._uri.data[folder_config["collective-dircap"]] = dumps([
            u"dirnode",
            {
                u"children": {
                    author: format_filenode(folder_config["upload-dircap"]),
                },
            },
        ])
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(root),
        )
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        # add a participant using the API
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "not-the-author": {"name": "kelly"},
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "Require input: author, personal_dmd"})
                    )
                )
            )
        )

    @given(
        author_names(),
        folder_names(),
    )
    def test_add_participant_wrong_author_json(self, author, folder_name):
        """
        Missing keys in 'participant' JSON for 'author' produces error
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        folder_config = magic_folder_config(
            create_local_author(author),
            FilePath(self.mktemp()),
            local_path,
        )

        root = create_fake_tahoe_root()
        # put our Collective DMD into the fake root
        root._uri.data[folder_config["collective-dircap"]] = dumps([
            u"dirnode",
            {
                u"children": {
                    author: format_filenode(folder_config["upload-dircap"]),
                },
            },
        ])
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(root),
        )
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        # add a participant using the API
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"not-the-name": "kelly"},
                    "personal_dmd": "fake",
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "'author' requires: name"})
                    )
                )
            )
        )

    @given(
        author_names(),
        folder_names(),
        tahoe_lafs_chk_capabilities(),
    )
    def test_add_participant_personal_dmd_non_dir(self, author, folder_name, personal_dmd):
        """
        When a new Personal DMD is passed that is not a directory
        capability an error is produced.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        folder_config = magic_folder_config(
            create_local_author(author),
            FilePath(self.mktemp()),
            local_path,
        )

        root = create_fake_tahoe_root()
        # put our Collective DMD into the fake root
        root._uri.data[folder_config["collective-dircap"]] = dumps([
            u"dirnode",
            {
                u"children": {
                    author: format_filenode(folder_config["upload-dircap"]),
                },
            },
        ])
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(root),
        )
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        # add a participant using the API
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd,
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "personal_dmd must be a directory-capability"})
                    )
                )
            )
        )

    @given(
        author_names(),
        folder_names(),
        tahoe_lafs_dir_capabilities(),
    )
    def test_add_participant_personal_dmd_writable(self, author, folder_name, personal_dmd):
        """
        If the added Personal DMD is read-write an error is signaled
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        folder_config = magic_folder_config(
            create_local_author(author),
            FilePath(self.mktemp()),
            local_path,
        )

        root = create_fake_tahoe_root()
        # put our Collective DMD into the fake root
        root._uri.data[folder_config["collective-dircap"]] = dumps([
            u"dirnode",
            {
                u"children": {
                    author: format_filenode(folder_config["upload-dircap"]),
                },
            },
        ])
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(root),
        )
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        # add a participant using the API
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd,
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "personal_dmd must be read-only"})
                    )
                )
            )
        )

    @given(
        author_names(),
        folder_names(),
    )
    def test_participant_list_internal_error(self, author, folder_name):
        """
        Listing participants reports a failure if there is an unexpected
        internal error.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        folder_config = magic_folder_config(
            create_local_author(author),
            FilePath(self.mktemp()),
            local_path,
        )

        # Arrange to have an "unexpected" error happen
        class ErrorClient(object):
            def __call__(self, *args, **kw):
                raise Exception("an unexpected error")
            def __getattr__(self, *args):
                return self
        tahoe_client = ErrorClient()

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        # list the participants
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"GET",
                self.url.child(folder_name, "participants"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(INTERNAL_SERVER_ERROR),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "unexpected error processing request"})
                    )
                )
            )
        )

    @given(
        author_names(),
        folder_names(),
        tahoe_lafs_readonly_dir_capabilities(),
    )
    def test_add_participant_internal_error(self, author, folder_name, personal_dmd):
        """
        An internal error on participant adding is returned when something
        truly unexpected happens.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        folder_config = magic_folder_config(
            create_local_author(author),
            FilePath(self.mktemp()),
            local_path,
        )

        # Arrange to have an "unexpected" error happen
        class ErrorClient(object):
            def __call__(self, *args, **kw):
                raise Exception("an unexpected error")
            def __getattr__(self, *args):
                return self
        tahoe_client = ErrorClient()

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=tahoe_client,
        )

        # add a participant using the API
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                b"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd,
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(INTERNAL_SERVER_ERROR),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "unexpected error processing request"})
                    )
                )
            )
        )
