# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.web``.
"""

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
    example,
)

from hypothesis.strategies import (
    lists,
    text,
    binary,
    integers,
    dictionaries,
    sampled_from,
)

from testtools.matchers import (
    AfterPreprocessing,
    AllMatch,
    ContainsDict,
    Equals,
    Contains,
    IsInstance,
    MatchesAny,
    MatchesDict,
    MatchesListwise,
    MatchesPredicate,
    StartsWith,
)
from testtools.twistedsupport import (
    succeeded,
    has_no_result,
)

from twisted.internet.defer import Deferred
from twisted.web.http import (
    BAD_REQUEST,
    CONFLICT,
    CREATED,
    INTERNAL_SERVER_ERROR,
    NOT_ACCEPTABLE,
    NOT_ALLOWED,
    NOT_FOUND,
    NOT_IMPLEMENTED,
    OK,
    UNAUTHORIZED,
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
from twisted.python import runtime

from nacl.encoding import (
    Base32Encoder,
)

from treq.testing import (
    StubTreq,
)

from .common import (
    skipIf,
    SyncTestCase,
)
from .fixtures import MagicFolderNode
from .matchers import (
    matches_response,
    header_contains,
    is_hex_uuid,
    matches_flushed_traceback
)

from .strategies import (
    path_segments,
    folder_names,
    relative_paths,
    tokens,
    author_names,
)

from ..config import (
    Conflict,
)
from ..web import (
    magic_folder_resource,
    APIv1,
)
from ..util.file import (
    PathState,
    seconds_to_ns,
)
from ..util.capabilities import (
    random_immutable,
)
from ..client import (
    authorized_request,
    url_to_bytes,
)
from ..tahoe_client import (
    create_tahoe_client,
    InsufficientStorageServers,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from ..snapshot import (
    RemoteSnapshot,
    create_local_author,
)
from .strategies import (
    tahoe_lafs_readonly_dir_capabilities,
    tahoe_lafs_dir_capabilities,
    tahoe_lafs_chk_capabilities,
    remote_snapshots,
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
            u"application/binary",
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
            b"Authorization": b"Bearer " + auth_token,
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


def treq_for_folders(
    reactor, basedir, auth_token, folders, start_folder_services, tahoe_client=None
):
    """
    Construct a ``treq``-module-alike which is hooked up to a Magic Folder
    service with Magic Folders like the ones given.

    See :py:`MagicFolderNode.create` for a description of the arguments.

    :return: An object like the ``treq`` module.
    """
    return MagicFolderNode.create(
        reactor, basedir, auth_token, folders, start_folder_services, tahoe_client
    ).http_client


def magic_folder_config(author_name, local_directory, admin=True):
    # see also treq_for_folders() where these dicts are turned into
    # real magic-folder configs
    return {
        "magic-path": local_directory,
        "author-name": author_name,
        "poll-interval": 60,
        "scan-interval": None,
        "admin": admin,
    }


class MagicFolderTests(SyncTestCase):
    """
    Tests for ``/v1/magic-folder``.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    def setUp(self):
        super(MagicFolderTests, self).setUp()
        self.author_name = u"alice"

    @given(
        sampled_from([u"PUT", u"PATCH", u"DELETE", u"OPTIONS"]),
    )
    def test_method_not_allowed(self, method):
        """
        A request to **/v1/magic-folder** with a method other than **GET** or **POST**
        receives a NOT ALLOWED or NOT IMPLEMENTED response.
        """
        treq = treq_for_folders(Clock(), FilePath(self.mktemp()), AUTH_TOKEN, {}, False)
        self.assertThat(
            authorized_request(treq, AUTH_TOKEN, method, self.url),
            succeeded(
                matches_response(
                    code_matcher=MatchesAny(
                        Equals(NOT_ALLOWED),
                        Equals(NOT_IMPLEMENTED),
                    ),
                    # there's some inscrutible error from twisted or
                    # klein about "_DataLoss()" if we try to read the
                    # body from this reply .. probably because this is
                    # a sync test and thus depends on the
                    # implementation, which must have changed?
                    body_matcher=None,
                ),
            ),
        )

    @given(
        folder_names(),
        # We need absolute paths but at least we can make them beneath the
        # test working directory.
        relative_paths().map(FilePath),
        integers(min_value=1),
    )
    def test_add_folder(self, folder_name, folder_path, poll_interval):
        """
        A request for **POST /v1/magic-folder** receives a response.
        """
        folder_path.makedirs(ignoreExistingDirectory=True)

        basedir = FilePath(self.mktemp())
        treq = treq_for_folders(
            Clock(),
            basedir,
            AUTH_TOKEN,
            {},
            False,
        )

        self.assertThat(
            authorized_request(treq, AUTH_TOKEN, u"POST", self.url, dumps({
                'name': folder_name,
                'author_name': self.author_name,
                'local_path': folder_path.path,
                'poll_interval': 60,
                'scan_interval': None,
            }).encode("utf8")),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    headers_matcher=header_contains({
                        b"Content-Type": Equals([b"application/json"]),
                    }),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({}),
                    ),
                ),
            ),
        )

    @given(
        folder_names(),
    )
    def test_add_folder_not_existing(self, folder_name):
        """
        A request for **POST /v1/magic-folder** with a path that does not exist
        fails with BAD REQUEST.
        """
        folder_path = FilePath(self.mktemp())

        basedir = FilePath(self.mktemp())
        treq = treq_for_folders(
            Clock(),
            basedir,
            AUTH_TOKEN,
            {},
            False,
        )

        self.assertThat(
            authorized_request(treq, AUTH_TOKEN, u"POST", self.url, dumps({
                'name': folder_name,
                'author_name': self.author_name,
                'local_path': folder_path.path,
                'poll_interval': 60,
                'scan_interval': None,
            }).encode("utf8")),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict(
                            {
                                "reason": Contains("does not exist"),
                            }
                        ),
                    ),
                ),
            ),
        )

    def test_add_folder_invalid_json(self):
        """
        A request for **POST /v1/magic-folder** that does not have a JSON body
        fails with BAD REQUEST.
        """
        treq = treq_for_folders(Clock(), FilePath(self.mktemp()), AUTH_TOKEN, {}, False)
        self.assertThat(
            authorized_request(
                treq, AUTH_TOKEN, "POST", self.url, "not-json".encode("utf-8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict(
                            {
                                "reason": StartsWith("Could not load JSON: "),
                            }
                        ),
                    ),
                ),
            ),
        )

    def test_add_folder_illegal_scan_interval(self):
        """
        A request for **POST /v1/magic-folder** that has a negative
        scan_interval fails with NOT ACCEPTABLE.
        """
        treq = treq_for_folders(object(), FilePath(self.mktemp()), AUTH_TOKEN, {}, False)
        self.assertThat(
            authorized_request(
                treq, AUTH_TOKEN, "POST", self.url, dumps({
                    'name': 'valid',
                    'author_name': 'author',
                    'local_path': 'foo',
                    'poll_interval': 60,
                    'scan_interval': -123,
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict(
                            {
                                "reason": StartsWith("scan_interval must be positive integer or null"),
                            }
                        ),
                    ),
                ),
            ),
        )


    def test_add_folder_tahoe_degraded(self):
        """
        A request for **POST /v1/magic-folder** while TahoeLAFS isn't
        connected to enough servers fails with NOT ACCEPTABLE.
        """
        tahoe_root = create_fake_tahoe_root()
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(tahoe_root),
        )
        tahoe_client.mutables_bad(InsufficientStorageServers(1, 4))
        treq = treq_for_folders(
            object(), FilePath(self.mktemp()), AUTH_TOKEN, {}, False, tahoe_client,
        )
        self.assertThat(
            authorized_request(
                treq, AUTH_TOKEN, "POST", self.url, dumps({
                    'name': 'valid',
                    'author_name': 'author',
                    'local_path': 'foo',
                    'poll_interval': 60,
                    'scan_interval': 60,
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(INTERNAL_SERVER_ERROR),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict(
                            {
                                "reason": Equals("Wanted 4 storage-servers but have 1"),
                            }
                        ),
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
        for path in folders.values():
            # Fix it so non-ASCII works reliably. :/ This is fine here but we
            # leave the original as text mode because that works better with
            # the config/database APIs.
            path.makedirs(ignoreExistingDirectory=True)

        basedir = FilePath(self.mktemp())
        node = MagicFolderNode.create(
            Clock(),
            basedir,
            AUTH_TOKEN,
            {
                name: magic_folder_config(self.author_name, path_u)
                for (name, path_u)
                in folders.items()
            },
            False,
        )

        expected_folders = {
            name: node.global_config.get_magic_folder(name)
            for name in node.global_config.list_magic_folders()
        }

        self.assertThat(
            authorized_request(node.http_client, AUTH_TOKEN, u"GET", self.url),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    headers_matcher=header_contains({
                        b"Content-Type": Equals([b"application/json"]),
                    }),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({
                            name: {
                                u"name": name,
                                u"author": {
                                    u"name": config.author.name,
                                    u"verify_key": config.author.verify_key.encode(Base32Encoder).decode("utf8"),
                                },
                                u"magic_path": config.magic_path.path,
                                u"stash_path": config.stash_path.path,
                                u"poll_interval": config.poll_interval,
                                u"scan_interval": config.scan_interval,
                                u"is_admin": config.is_admin(),
                            }
                            for name, config
                            in expected_folders.items()
                        }),
                    ),
                ),
            ),
        )


class MagicFolderInstanceTests(SyncTestCase):
    """
    Tests for ``/v1/magic-folder/<folder-name>``.
    """

    url = DecodedURL.from_text("http://example.invalid./v1/magic-folder")

    def setUp(self):
        super(MagicFolderInstanceTests, self).setUp()
        self.author_name = "alice"

    @given(
        sampled_from([u"GET", u"POST", u"PUT", u"PATCH", u"OPTIONS"]),
        folder_names(),
    )
    def test_method_not_allowed(self, method, folder_name):
        """
        A request to **/v1/magic-folder/<folder-name>** with a method other than **DELETE**
        receives a NOT ALLOWED or NOT IMPLEMENTED response.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: magic_folder_config(self.author_name, local_path),
            },
            False,
        )

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                method,
                self.url.child(folder_name),
            ),
            succeeded(
                matches_response(
                    code_matcher=MatchesAny(
                        Equals(NOT_ALLOWED),
                        Equals(NOT_IMPLEMENTED),
                    ),
                    body_matcher=None,  # don't try to read body at all
                ),
            ),
        )

    @given(
        folder_names(),
    )
    def test_leave_folder(self, folder_name):
        """
        A request for **DELETE /v1/magic-folder/<folder-name>** succeeds.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: magic_folder_config(self.author_name, local_path, admin=False),
            },
            False,
        )

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                "DELETE",
                self.url.child(folder_name),
                b"{}",
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict({}),
                    ),
                ),
            ),
        )

    # Note: It may be possible to get this test to fail on windows, using a different
    # means of causing the cleanup to fail
    @skipIf(runtime.platformType == "win32", "windows does not have unix-like permissions")
    @given(
        folder_names(),
    )
    def test_leave_folder_cant_cleanup(self, folder_name):

        """
        A request for **DELETE /v1/magic-folder/<folder-name>** fails, if
        we can't delete the state or stash directories.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        basedir = FilePath(self.mktemp())
        node = MagicFolderNode.create(
            Clock(),
            basedir,
            AUTH_TOKEN,
            {
                folder_name: magic_folder_config(self.author_name, local_path, admin=False),
            },
            False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        # Make the magic-folder config directory readonly (and restore it after
        # the test) so that removing the folder state directory fails.
        # Note that we use an in-memory global config database, otherwise
        # this would also cause write to it to fail.
        basedir.chmod(0o500)
        self.addCleanup(basedir.chmod, 0o700)

        folder_service = node.global_service.get_folder_service(folder_name)
        folder_config_dir = folder_service.config.stash_path.parent()

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                "DELETE",
                self.url.child(folder_name),
                b"{}",
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(INTERNAL_SERVER_ERROR),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict(
                            {
                                "reason": Contains(
                                    "Problems while removing state directories"
                                ),
                                "details": MatchesDict(
                                    {
                                        folder_config_dir.path: Contains(
                                            "Permission denied"
                                        )
                                    }
                                ),
                            }
                        ),
                    ),
                ),
            ),
        )

    @given(
        folder_names(),
    )
    def test_leave_folder_admin(self, folder_name):
        """
        A request for **DELETE /v1/magic-folder/<folder-name>** succeeds.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: magic_folder_config(self.author_name, local_path),
            },
            False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                "DELETE",
                self.url.child(folder_name),
                b"{}",
            ),
            succeeded(
                matches_response(
                    code_matcher=MatchesAny(
                        Equals(CONFLICT),
                    ),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict({"reason": Contains("holds a write capability")}),
                    ),
                ),
            ),
        )

    @given(
        folder_names(),
    )
    def test_leave_folder_admin_really(self, folder_name):
        """
        A request for **DELETE /v1/magic-folder/<folder-name>** succeeds.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: magic_folder_config(self.author_name, local_path),
            },
            False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                "DELETE",
                self.url.child(folder_name),
                dumps(
                    {
                        "really-delete-write-capability": True,
                    }
                ).encode("utf8"),
            ),
            succeeded(
                matches_response(
                    code_matcher=MatchesAny(
                        Equals(OK),
                    ),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict({}),
                    ),
                ),
            ),
        )

    @given(
        folder_names(),
    )
    def test_leave_folder_not_existing(self, folder_name):
        """
        A request for **DELETE /v1/magic-folder/<folder-name>** with a non-existant
        magic-folder fails with NOT FOUND.
        """
        basedir = FilePath(self.mktemp())
        node = MagicFolderNode.create(
            Clock(),
            basedir,
            AUTH_TOKEN,
            {},
            False,
        )

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                "DELETE",
                self.url.child(folder_name),
                b"{}",
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(NOT_FOUND),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict(
                            {
                                "reason": StartsWith("No such magic-folder"),
                            }
                        ),
                    ),
                ),
            ),
        )


class RedirectTests(SyncTestCase):
    """
    Test for handling redirects from werkzeug.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    @given(
        folder_names(),
    )
    @example("%25")
    @example(":")
    @example(",")
    def test_merge_slashes(self, folder_name):
        """
        Test that redirects are generated and have a JSON body.

        We test this by using a `//` in the URL path, which werkzeug
        redirects to not have the `/`.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config("alice", local_path)},
            start_folder_services=False,
        )

        # We apply .to_uri() here since hyperlink and werkzeug disagree
        # on which characters to encocde.
        # Seee https://github.com/python-hyper/hyperlink/issues/168
        def to_iri(url_bytes):
            return DecodedURL.from_text(url_bytes.decode("utf8")).to_iri()
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"GET",
                self.url.child(folder_name, "", "file-status"),
            ),
            succeeded(
                matches_response(
                    # Maybe this could be BAD_REQUEST instead, sometimes, if
                    # the path argument was bogus somehow.
                    code_matcher=Equals(200),
                    headers_matcher=header_contains(
                        {
                            b"Content-Type": Equals([b"application/json"]),
                        }
                    ),
                ),
            ),
        )

    def test_werkzeug_issue_2157_fix(self):
        """
        Ensure that the only redirects that werkzeug will generate are merging slashes.

        This ensures that the workaround to
        https://github.com/pallets/werkzeug/issues/2157 is correct.
        """
        self.assertThat(
            APIv1.app.url_map.iter_rules(),
            AllMatch(
                MatchesPredicate(
                    lambda rule: rule.is_leaf and not rule.alias,
                    "Rule %r is not a leaf, has an alias, or has a redirect specified. "
                    "This will break our fix for https://github.com/pallets/werkzeug/issues/2157"
                )
            ),
        )



class ScanFolderTests(SyncTestCase):
    """
    Tests for scanning an existing Magic Folder.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    def setUp(self):
        super(ScanFolderTests, self).setUp()
        # TODO: Rather than using this fake, MagicFolderService should
        # take a cooperator that is used by all the child services.
        # We use this fake IReactorTime instead, which causes ScannerService
        # to create a cooperator that runs it's tasks to completion immediately.
        class ImmediateClock(Clock, object):
            def callLater(self, delay, f, *args, **kwargs):
                if delay == 0:
                    f(*args, **kwargs)
                else:
                    super(ImmediateClock, self).callLater(delay, f, *args, **kwargs)

        self.clock = ImmediateClock()

    @given(
        author_names(),
        folder_names(),
        relative_paths(),
        binary(),
    )
    def test_wait_for_completion(self, author_name, folder_name, path_in_folder, some_content):
        """
        A **PUT** request to **/v1/magic-folder/:folder-name/scan-local** does not receive a
        response before the snapshot has been created in the local database.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        some_file = local_path.preauthChild(path_in_folder)
        some_file.parent().makedirs(ignoreExistingDirectory=True)
        some_file.setContent(some_content)

        node = MagicFolderNode.create(
            self.clock,
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author_name, local_path)},
            # The interesting behavior of this test hinges on this flag.  We
            # decline to start the folder services here.  Therefore, no local
            # snapshots will ever be created.  This lets us observe the
            # request in a state where it is waiting to receive its response.
            # This demonstrates that the response is not delivered before the
            # local snapshot is created.  See test_create_snapshot for the
            # alternative case.
            start_folder_services=False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"PUT",
                self.url.child(folder_name, "scan-local"),
            ),
            has_no_result(),
        )

    @given(
        author_names(),
        folder_names(),
        relative_paths(),
        binary(),
    )
    def test_scan_folder(self, author_name, folder_name, path_in_folder, some_content):
        """
        A **PUT** to **/v1/magic-folder/:folder-name/scan-local** creates a new local
        snapshot for a file in the named folder.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        some_file = local_path.preauthChild(path_in_folder)
        some_file.parent().makedirs(ignoreExistingDirectory=True)
        some_file.setContent(some_content)

        node = MagicFolderNode.create(
            self.clock,
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author_name, local_path)},
            # Unlike test_wait_for_completion above we start the folder
            # services.  This will allow the local snapshot to be created and
            # our request to receive a response.
            start_folder_services=True,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"PUT",
                self.url.child(folder_name, "scan-local"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({}),
                    )
                ),
            ),
        )

        folder_config = node.global_config.get_magic_folder(folder_name)
        snapshot_paths = folder_config.get_all_snapshot_paths()
        self.assertThat(
            snapshot_paths,
            Equals({path_in_folder}),
        )

    def test_snapshot_no_folder(self):
        """
        An error results from using /v1/magic-folder/<folder-name>/scan-local API on
        non-existent folder.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {},
            start_folder_services=False,
        )

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"PUT",
                self.url.child("a-folder-that-doesnt-exist").child('scan-local'),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(NOT_FOUND),
                    body_matcher=AfterPreprocessing(
                        loads,
                        ContainsDict({
                            "reason": StartsWith("No such magic-folder"),
                        })
                    )
                ),
            )
        )


class CreateSnapshotTests(SyncTestCase):
    """
    Tests for creating a new snapshot in an existing Magic Folder using a
    **POST**.
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    @given(
        author_names(),
        folder_names(),
        relative_paths(),
        binary(),
    )
    def test_wait_for_completion(self, author, folder_name, path_in_folder, some_content):
        """
        A **POST** request to **/v1/magic-folder/:folder-name/snapshot** does not receive a
        response before the snapshot has been created in the local database.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        some_file = local_path.preauthChild(path_in_folder)
        some_file.parent().makedirs(ignoreExistingDirectory=True)
        some_file.setContent(some_content)

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, local_path)},
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
                node.http_client,
                AUTH_TOKEN,
                u"POST",
                self.url.child(folder_name, "snapshot").set(u"path", path_in_folder),
            ),
            has_no_result(),
        )

    @given(
        author_names(),
        folder_names(),
        relative_paths(),
    )
    def test_create_fails(self, author, folder_name, path_in_folder):
        """
        If a local snapshot cannot be created, a **POST** to
        **/v1/magic-folder/<folder-name>/snapshot** receives a response with an HTTP error
        code.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        # You may not create a snapshot of a directory.
        not_a_file = local_path.preauthChild(path_in_folder)
        not_a_file.makedirs(ignoreExistingDirectory=True)

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, local_path)},
            # This test carefully targets a failure mode that doesn't require
            # the service to be running.
            start_folder_services=False,
        )
        # note: no .addCleanup needed because we're not starting the services

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
                self.url.child(folder_name, "snapshot").set(u"path", path_in_folder),
            ),
            succeeded(
                matches_response(
                    # Maybe this could be BAD_REQUEST or INTERNAL_SERVER_ERROR
                    # instead, sometimes, if the path argument was bogus or
                    # somehow the path could not be read.
                    code_matcher=Equals(NOT_ACCEPTABLE),
                    headers_matcher=header_contains({
                        b"Content-Type": Equals([b"application/json"]),
                    }),
                    body_matcher=AfterPreprocessing(
                        loads,
                        ContainsDict({
                            u"reason": IsInstance(str),
                        }),
                    ),
                ),
            ),
        )

    @given(
        author_names(),
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

        some_file = local_path.preauthChild(path_in_folder)
        some_file.parent().makedirs(ignoreExistingDirectory=True)
        some_file.setContent(some_content)

        # collect our "never" Deferreds so we can cancel them"
        never_d = []

        # Pass a tahoe client that never responds, so the created
        # snapshot is not uploaded.
        class NeverClient(object):
            def __call__(self, *args, **kw):
                d = Deferred()
                never_d.append(d)
                return d
            def __getattr__(self, *args):
                return self
        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, local_path)},
            tahoe_client=NeverClient(),
            # Unlike test_wait_for_completion above we start the folder
            # services.  This will allow the local snapshot to be created and
            # our request to receive a response.
            start_folder_services=True,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True
        self.addCleanup(node.cleanup)

        folder_config = node.global_config.get_magic_folder(folder_name)

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
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
                node.http_client,
                AUTH_TOKEN,
                u"GET",
                DecodedURL.from_text(u"http://example.invalid./v1/snapshot")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    headers_matcher=header_contains({
                        b"Content-Type": Equals([b"application/json"]),
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
                                        u"author": Equals(folder_config.author.to_remote_author().to_json()),
                                    }),
                                ]),
                            }),
                        }),
                    ),
                ),
            ),
        )
        # we're done, cancel everything that's never going to have an answer
        for d in never_d:
            d.cancel()

    @given(
        author_names(),
        folder_names(),
        sampled_from([u"..", u"foo/../..", u"/tmp/foo"]),
        binary(),
    )
    def test_create_snapshot_fails(self, author, folder_name, path_outside_folder, some_content):
        """
        A **POST** to **/v1/magic-folder/:folder-name/snapshot** with a **path** query argument
        fails if the **path** is outside the magic-folder
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {folder_name: magic_folder_config(author, local_path)},
            # Unlike test_wait_for_completion above we start the folder
            # services.  This will allow the local snapshot to be created and
            # our request to receive a response.
            start_folder_services=True,
        )
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"POST",
                self.url.child(folder_name, "snapshot").set(u"path", path_outside_folder),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(NOT_ACCEPTABLE),
                ),
            ),
        )

    def test_add_snapshot_no_folder(self):
        """
        An error results using /v1/snapshot API on non-existent
        folder.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {},
            start_folder_services=False,
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"POST",
                self.url.child("a-folder-that-doesnt-exist").child('snapshot'),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(NOT_FOUND),
                    body_matcher=AfterPreprocessing(
                        loads,
                        ContainsDict({
                            "reason": StartsWith("No such magic-folder"),
                        })
                    )
                ),
            )
        )


class ParticipantsTests(SyncTestCase):
    """
    Tests relating to the '/v1/participants/<folder>` API
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    def test_participants_no_folder(self):
        """
        An error results using /v1/magic-folder/:folder-name/participants API on non-existent
        folder.
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {},
            start_folder_services=False,
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"GET",
                self.url.child("a-folder-that-doesnt-exist", "participants"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(NOT_FOUND),
                    body_matcher=AfterPreprocessing(
                        loads,
                        ContainsDict({
                            "reason": StartsWith("No such magic-folder"),
                        })
                    )
                ),
            )
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"POST",
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

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: magic_folder_config(
                    "iris",
                    local_path,
                ),
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        folder_config = node.global_config.get_magic_folder(folder_name)
        # we can't add a new participant if their DMD is the same as
        # one we already have .. and because Hypothesis is 'sneaky' we
        # have to make sure it's not our collective, either
        assume(personal_dmd != folder_config.upload_dircap)
        assume(personal_dmd != folder_config.upload_dircap.to_readonly())
        assume(personal_dmd != folder_config.collective_dircap)
        assume(personal_dmd != folder_config.collective_dircap.to_readonly())

        # add a participant using the API
        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd.danger_real_capability_string(),
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
                node.http_client,
                AUTH_TOKEN,
                u"GET",
                self.url.child(folder_name, "participants"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(OK),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({
                            u"iris": {
                                u"personal_dmd": folder_config.upload_dircap.to_readonly().danger_real_capability_string(),
                            },
                            u'kelly': {
                                u'personal_dmd': personal_dmd.danger_real_capability_string(),
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
    def test_add_participant_invalid(self, author, folder_name):
        """
        Missing keys in 'participant' JSON produces error
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()
        folder_config = magic_folder_config(
            author,
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        # add a participant using the API
        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
                self.url.child(folder_name, "participants"),
                "not-json".encode("utf-8"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        MatchesDict({"reason": StartsWith("Could not load JSON: ")})
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
            author,
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        # add a participant using the API
        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
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
            author,
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        # add a participant using the API
        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
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
            author,
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        # add a participant using the API
        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd.danger_real_capability_string(),
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "personal_dmd must be a read-only directory capability."})
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
            author,
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service(folder_name).file_factory._synchronous = True

        # add a participant using the API
        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd.danger_real_capability_string(),
                }).encode("utf8")
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(BAD_REQUEST),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({"reason": "personal_dmd must be a read-only directory capability."})
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
            author,
            local_path,
        )

        # Arrange to have an "unexpected" error happen
        class ErrorClient(object):
            def __call__(self, *args, **kw):
                raise Exception("an unexpected error")
            def __getattr__(self, *args):
                return self

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                folder_name: folder_config,
            },
            start_folder_services=False,
            tahoe_client=ErrorClient(),
        )

        # list the participants
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"GET",
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

        if False:
            # XXX FIXME eliot upgrade changed behavior?
            self.assertThat(
                self.eliot_logger.flushTracebacks(Exception),
                MatchesListwise([
                    matches_flushed_traceback(Exception, "an unexpected error")
                ]),
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
            author,
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
                u"POST",
                self.url.child(folder_name, "participants"),
                dumps({
                    "author": {"name": "kelly"},
                    "personal_dmd": personal_dmd.danger_real_capability_string(),
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

        if False:
            # something still up, sending these to non-test logger
            # somehow.
            self.assertThat(
                self.eliot_logger.flushTracebacks(Exception),
                MatchesListwise([
                    matches_flushed_traceback(Exception, "an unexpected error")
                ]),
            )


class FileStatusTests(SyncTestCase):
    """
    Tests relating to the '/v1/magic-folder/<folder>/file-status` API
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    def test_empty(self):
        """
        We return empty information for an empty magic-folder
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        folder_config = magic_folder_config(
            "louise",
            local_path,
        )

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                "default": folder_config,
            },
            start_folder_services=False,
        )

        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"GET",
                self.url.child("default", "file-status"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(200),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals([
                        ]),
                    )
                ),
            )
        )

    def test_one_item(self):
        """
        Appropriate information is returned when we have a file-status for
        one file in our config/db
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        folder_config = magic_folder_config(
            "kristi",
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                "default": folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service("default").file_factory._synchronous = True

        mf_config = node.global_config.get_magic_folder("default")
        mf_config._get_current_timestamp = lambda: 42.0
        mf_config.store_currentsnapshot_state(
            "foo",
            PathState(123, seconds_to_ns(1), seconds_to_ns(2)),
        )

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"GET",
                self.url.child("default", "file-status"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(200),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals([
                            {
                                "mtime": 1,
                                "size": 123,
                                "relpath": "foo",
                                "last-updated": 42,
                                "last-upload-duration": None,
                            },
                        ]),
                    )
                ),
            )
        )


class ConflictStatusTests(SyncTestCase):
    """
    Tests relating to the '/v1/magic-folder/<folder>/conflicts` API
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    def test_empty(self):
        """
        A folder with no conflicts reflects that in the status
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        folder_config = magic_folder_config(
            "louise",
            local_path,
        )

        treq = treq_for_folders(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                "default": folder_config,
            },
            start_folder_services=False,
        )

        # external API
        self.assertThat(
            authorized_request(
                treq,
                AUTH_TOKEN,
                u"GET",
                self.url.child("default", "conflicts"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(200),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({}),
                    )
                ),
            )
        )

    def test_one_conflict(self):
        """
        Appropriate information is returned when we have a conflict with
        one author
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        folder_config = magic_folder_config(
            "marta",
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                "default": folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service("default").file_factory._synchronous = True

        mf_config = node.global_config.get_magic_folder("default")
        mf_config._get_current_timestamp = lambda: 42.0
        mf_config.store_currentsnapshot_state(
            "foo",
            PathState(123, seconds_to_ns(1), seconds_to_ns(2)),
        )

        # internal API for "no conflict yet"
        self.assertThat(
            mf_config.list_conflicts_for("foo"),
            Equals([])
        )

        snap = RemoteSnapshot(
            "foo",
            create_local_author("nelli"),
            {"relpath": "foo", "modification_time": 1234},
            random_immutable(directory=True),
            [],
            random_immutable(),
            random_immutable(),
        )

        mf_config.add_conflict(snap)

        # internal API
        self.assertThat(
            mf_config.list_conflicts_for("foo"),
            Equals([Conflict(snap.capability, "nelli")])
        )

        # external API
        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"GET",
                self.url.child("default", "conflicts"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(200),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals({
                            "foo": ["nelli"],
                        }),
                    )
                ),
            )
        )


class TahoeObjectsTests(SyncTestCase):
    """
    Tests relating to the '/v1/magic-folder/<folder>/tahoe-objects` API
    """
    url = DecodedURL.from_text(u"http://example.invalid./v1/magic-folder")

    @given(
        remote_snapshots(),
    )
    def test_one_item(self, remote_snap):
        """
        Appropriate information is returned when we have one file in our
        config/db
        """
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        folder_config = magic_folder_config(
            "jenni",
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                "default": folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service("default").file_factory._synchronous = True

        mf_config = node.global_config.get_magic_folder("default")
        mf_config._get_current_timestamp = lambda: 42.0
        mf_config.store_downloaded_snapshot(
            "foo",
            remote_snap,
            PathState(123, seconds_to_ns(1), seconds_to_ns(2)),
        )

        expected_sizes = [
            cap.size
            for cap in [
                    remote_snap.capability,
                    remote_snap.content_cap,
                    remote_snap.metadata_cap,
            ]
        ]

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"GET",
                self.url.child("default", "tahoe-objects"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(200),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals(expected_sizes),
                    )
                ),
            )
        )

    @given(
        remote_snapshots(),
    )
    def test_deleted_item(self, remote_snap):
        """
        object-size information is returned for deleted items
        """
        # make it a delete .. it's a little weird to have a delete
        # with no "content" parent (semantically) but for the purposes
        # of this test that is sufficient.
        remote_snap.content_cap = None
        local_path = FilePath(self.mktemp())
        local_path.makedirs()

        folder_config = magic_folder_config(
            "kai",
            local_path,
        )

        node = MagicFolderNode.create(
            Clock(),
            FilePath(self.mktemp()),
            AUTH_TOKEN,
            {
                "default": folder_config,
            },
            start_folder_services=False,
        )
        node.global_service.get_folder_service("default").file_factory._synchronous = True

        mf_config = node.global_config.get_magic_folder("default")
        mf_config._get_current_timestamp = lambda: 42.0
        mf_config.store_downloaded_snapshot(
            "foo",
            remote_snap,
            PathState(123, seconds_to_ns(1), seconds_to_ns(2)),
        )

        expected_sizes = [
            cap.size
            for cap in [
                    remote_snap.capability,
                    remote_snap.metadata_cap,
            ]
        ]

        self.assertThat(
            authorized_request(
                node.http_client,
                AUTH_TOKEN,
                u"GET",
                self.url.child("default", "tahoe-objects"),
            ),
            succeeded(
                matches_response(
                    code_matcher=Equals(200),
                    body_matcher=AfterPreprocessing(
                        loads,
                        Equals(expected_sizes),
                    )
                ),
            )
        )
