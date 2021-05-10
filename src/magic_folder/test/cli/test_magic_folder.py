import json
import os.path
from io import (
    StringIO,
)

from testtools import (
    ExpectedException,
)
from testtools.content import (
    text_content,
)
from testtools.twistedsupport import (
    succeeded,
)
from testtools.matchers import (
    Contains,
    Equals,
    AfterPreprocessing,
    Always,
    ContainsDict,
    MatchesStructure,
)

from hyperlink import (
    DecodedURL,
)

from treq.testing import (
    StubTreq,
)

from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import usage
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.resource import (
    Resource,
)

from ... import cli as magic_folder_cli
from ...initialize import (
    magic_folder_initialize,
)
from ...config import (
    create_global_configuration,
    create_testing_configuration,
    load_global_configuration,
)
from ...client import (
    create_testing_http_client,
)
from ...endpoints import (
    CannotConvertEndpointError,
)
from ...snapshot import (
    create_local_author,
)
from ...list import (
    magic_folder_list,
)
from ...tahoe_client import (
    create_tahoe_client,
)

from ..common_util import (
    parse_cli,
)
from ..common import (
    AsyncTestCase,
    SyncTestCase,
)
from ..fixtures import (
    SelfConnectedClient,
    NodeDirectory,
)
from .common import (
    cli,
)


class ListMagicFolder(AsyncTestCase):
    """
    Tests for the command-line interface ``magic-folder list``.
    """
    @defer.inlineCallbacks
    def setUp(self):
        """
        Create a Tahoe-LAFS node which can contain some magic folder configuration
        and run it.
        """
        yield super(ListMagicFolder, self).setUp()
        self.client_fixture = SelfConnectedClient(reactor)
        yield self.client_fixture.use_on(self)

        self.tempdir = self.client_fixture.tempdir
        self.node_directory = self.client_fixture.node_directory

        # the Web APIs need a reference to a "global_service" .. which
        # is cli.MagicFolderService (a MultiService in fact). It
        # doesn't declare an interface, but only uses
        # "get_folder_service(folder_name)" .. so we'll duck-type it
        # instead

        # inside create_testing_configuration, the GlobalService is
        # hooked up to an in-process HTTP API root which is
        # interrogated for information. So, we want to control which
        # magic-folders it sees

        self.magic_folders = {}

        class GlobalService(object):
            def get_folder_service(s, name):
                return self.magic_folders[name]

        self.service = GlobalService()
        self.config = create_testing_configuration(
            FilePath(self.mktemp()),
            self.node_directory,
        )
        self.http_client = create_testing_http_client(
            reactor,
            self.config,
            self.service,
            lambda: self.config.api_token,
            create_tahoe_client(DecodedURL.from_text(u""), StubTreq(Resource())),
        )

    @defer.inlineCallbacks
    def test_list_none(self):
        """
        When there are no Magic Folders at all, the output of the list command
        reports this.
        """
        output = StringIO()
        yield magic_folder_list(reactor, self.config, self.http_client, output)
        self.assertThat(
            output.getvalue(),
            Contains(u"No magic-folders")
        )

    @defer.inlineCallbacks
    def test_list_none_json(self):
        """
        When there are no Magic Folders at all, the output of the list command
        reports this in JSON format if given ``--json``.
        """
        output = StringIO()
        yield magic_folder_list(reactor, self.config, self.http_client, output, as_json=True)
        self.assertThat(
            output.getvalue(),
            AfterPreprocessing(json.loads, Equals({}))
        )

    @defer.inlineCallbacks
    def test_list_some(self):
        """
        When there are Magic Folders, the output of the list command describes
        them.
        """
        folder_path = FilePath(self.mktemp())
        folder_path.makedirs()

        self.config.create_magic_folder(
            u"list-some-folder",
            folder_path,
            folder_path.child(u".state"),
            create_local_author(u"alice"),
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            1,
        )

        output = StringIO()
        yield magic_folder_list(reactor, self.config, self.http_client, output)
        self.expectThat(output.getvalue(), Contains(u"list-some-folder"))
        self.expectThat(output.getvalue(), Contains(folder_path.path))

    @defer.inlineCallbacks
    def test_list_some_json(self):
        """
        When there are Magic Folders, the output of the list command describes
        them in JSON format if given ``--json``.
        """
        folder_path = FilePath(self.mktemp())
        folder_path.makedirs()

        self.config.create_magic_folder(
            u"list-some-json-folder",
            folder_path,
            folder_path.child(u".state"),
            create_local_author(u"alice"),
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            1,
        )

        output = StringIO()
        yield magic_folder_list(
            reactor,
            self.config,
            self.http_client,
            output,
            as_json=True,
            include_secret_information=True,
        )

        self.expectThat(
            output.getvalue(),
            AfterPreprocessing(
                json.loads,
                ContainsDict({
                    u"list-some-json-folder": ContainsDict({
                        u"magic_path": Equals(folder_path.path),
                        u"poll_interval": Equals(1),
                        u"is_admin": Equals(False),
                        u"collective_dircap": Always(),
                        u"upload_dircap": Always(),
                    }),
                }),
            ),
        )


def addOutcomeDetails(testcase, outcome):
    testcase.addDetail(
        u"stdout",
        text_content(outcome.stdout),
    )
    testcase.addDetail(
        u"stderr",
        text_content(outcome.stderr),
    )
    testcase.addDetail(
        u"code",
        text_content(unicode(outcome.code)),
    )


class CreateMagicFolder(AsyncTestCase):
    @defer.inlineCallbacks
    def setUp(self):
        """
        Create a Tahoe-LAFS node which can contain some magic folder configuration
        and run it.
        """
        yield super(CreateMagicFolder, self).setUp()
        self.client_fixture = SelfConnectedClient(reactor)
        yield self.client_fixture.use_on(self)

        self.tempdir = self.client_fixture.tempdir
        self.config_dir = FilePath(self.mktemp())
        create_global_configuration(
            self.config_dir,
            u"tcp:4321",
            self.client_fixture.node_directory,
            u"tcp:localhost:4321",
        )

    @defer.inlineCallbacks
    def test_add_magic_folder(self):
        """
        Create a new magic folder with a nickname and local directory so
        that this folder is also invited and joined with the given nickname.
        """
        # Get a magic folder.
        magic_folder = self.tempdir.child(u"magic-folder")
        magic_folder.makedirs()

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--name", b"test",
                b"--author", b"test",
                magic_folder.asBytesMode().path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @defer.inlineCallbacks
    def test_create_duplicate_name(self):
        """
        Create a magic folder and if that succeeds, then create another
        magic folder with the same name and check if this results in an
        error.
        """
        # Get a magic folder.
        magic_folder = self.tempdir.child(u"magic-folder")
        magic_folder.makedirs()

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--name", b"foo",
                b"--author", b"test",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Always(),
        )

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--name", b"foo",
                b"--author", b"test",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "Already have a magic-folder named 'foo'",
            outcome.stderr
        )

    @defer.inlineCallbacks
    def test_add_leave_folder(self):
        """
        Create a magic folder and then leave the folder and check
        whether it was successful.
        """
        # Get a magic folder.
        magic_folder = self.tempdir.child(u"magic-folder")
        magic_folder.makedirs()

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--name", b"foo",
                b"--author", b"test",
                magic_folder.asBytesMode().path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
            str(outcome),
        )

        outcome = yield cli(
            self.config_dir, [
                b"leave",
                b"--name", b"foo",
                b"--really-delete-write-capability",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @defer.inlineCallbacks
    def test_leave_wrong_folder(self):
        """
        Create a magic folder with a specified name and then invoke
        the leave command with a different specified name. This should
        result in a failure.
        """
        # Get a magic folder.
        magic_folder = self.tempdir.child(u"magic-folder")
        magic_folder.makedirs()

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--author", b"test",
                b"--name", b"foo",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.config_dir, [
                b"leave",
                b"--name", b"bar",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "No such magic-folder 'bar'",
            outcome.stderr
        )

    @defer.inlineCallbacks
    def test_leave_no_folder(self):
        """
        Create a magic folder and then leave the folder. Leaving it again
        should result in an error.
        """
        # Get a magic folder.
        magic_folder = self.tempdir.child(u"magic-folder")
        magic_folder.makedirs()

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--name", b"foo",
                b"--author", b"alice",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.config_dir, [
                b"leave",
                b"--name", b"foo",
                b"--really-delete-write-capability",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.config_dir, [
                b"leave",
                b"--name", b"foo",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "No such magic-folder 'foo'",
            outcome.stderr
        )

    @defer.inlineCallbacks
    def test_leave_no_folders_at_all(self):
        """
        Leave a non-existant magic folder. This should result in
        an error.
        """
        outcome = yield cli(
            self.config_dir, [
                b"leave",
                b"--name", b"foo",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "No such magic-folder 'foo'",
            outcome.stderr
        )

    def test_help_synopsis(self):
        """
        Test if synonsis is defined for the help switch.
        """
        self.basedir = "cli/MagicFolder/help_synopsis"
        os.makedirs(self.basedir)

        o = magic_folder_cli.AddOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent.getSynopsis()

    def test_config_directory_is_file(self):
        """
        Using --config with a file is an error
        """
        confdir = FilePath(self.mktemp())
        with confdir.open("w") as f:
            f.write("dummy\n")

        outcome = yield cli(confdir, ["list"])
        self.assertThat(outcome.code, Equals(1))
        self.assertThat(outcome.stderr, Contains("Unable to load configuration"))

    @defer.inlineCallbacks
    def test_config_directory_empty(self):
        """
        A directory that is empty isn't valid for --config
        """
        confdir = FilePath(self.mktemp())
        confdir.makedirs()

        outcome = yield cli(confdir, ["list"])
        self.assertThat(outcome.code, Equals(1))
        self.assertThat(outcome.stderr, Contains("Unable to load configuration"))


class CreateErrors(SyncTestCase):

    def setUp(self):
        super(CreateErrors, self).setUp()
        self.temp = FilePath(self.mktemp())
        self.temp.makedirs()

    def test_poll_interval(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli(
                "add",
                "--name", "test",
                "--author", "test",
                "--poll-interval=frog",
                self.temp.path
            )
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")


class ClientEndpoint(SyncTestCase):
    """
    Tests related to the client-api-endpoint global config option
    """

    def setUp(self):
        super(ClientEndpoint, self).setUp()
        self.basedir = FilePath(self.mktemp())
        self.nodedir = self.useFixture(
            NodeDirectory(FilePath(self.mktemp()))
        )

    def test_convert_tcp(self):
        """
        a 'tcp:'-style endpoint can be autoconverted
        """
        config_d = magic_folder_initialize(self.basedir, u"tcp:5555", self.nodedir.path, None)
        self.assertThat(
            config_d,
            succeeded(
                MatchesStructure(
                    api_client_endpoint=Equals(u"tcp:127.0.0.1:5555"),
                )
            )
        )

    def test_convert_tcp_host(self):
        """
        a tcp: endpoint can be autoconverted with host
        """
        config_d = magic_folder_initialize(self.basedir, u"tcp:5555:interface=127.1.2.3", self.nodedir.path, None)
        self.assertThat(
            config_d,
            succeeded(
                MatchesStructure(
                    api_client_endpoint=Equals(u"tcp:127.1.2.3:5555"),
                )
            )
        )

    def test_convert_fail(self):
        """
        unknown endpoint conversion fails
        """
        with ExpectedException(CannotConvertEndpointError):
            magic_folder_initialize(self.basedir, u"onion:555", self.nodedir.path, None)

    def test_convert_unix(self):
        """
        a tcp: endpoint can be autoconverted with host
        """
        config_d = magic_folder_initialize(self.basedir, u"unix:/var/run/x", self.nodedir.path, None)
        self.assertThat(
            config_d,
            succeeded(
                MatchesStructure(
                    api_client_endpoint=Equals(u"unix:/var/run/x"),
                )
            )
        )

    def test_set_client_endpoint(self):
        """
        setting the client endpoint succeeds with a valid endpoint
        """
        config = create_global_configuration(
            self.basedir,
            u"tcp:5555",
            self.nodedir.path,
            u"tcp:localhost:1234",
        )
        config.api_client_endpoint = u"tcp:localhost:5555"
        self.assertThat(
            config.api_client_endpoint,
            Equals(u"tcp:localhost:5555")
        )
        # confirm the actual database has changed by loading again
        config2 = load_global_configuration(self.basedir)
        self.assertThat(
            config2.api_client_endpoint,
            Equals(u"tcp:localhost:5555")
        )

    def test_set_invalid_client_endpoint(self):
        """
        setting the client endpoint fails (if it is invalid)
        """
        config = create_global_configuration(
            self.basedir,
            u"tcp:5555",
            self.nodedir.path,
            u"tcp:localhost:1234",
        )
        with ExpectedException(ValueError, "Unknown endpoint type.*"):
            config.api_client_endpoint = u"invalid:"

        # also confirm the database *didn't* get changed and has the
        # original value still
        config2 = load_global_configuration(self.basedir)
        self.assertThat(
            config2.api_client_endpoint,
            Equals(u"tcp:localhost:1234")
        )


class JoinErrors(AsyncTestCase):
    def test_poll_interval(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("join", "--author", "test-dummy", "--poll-interval=frog", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("join", "--author", "test-dummy", "--poll-interval=-2", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")
