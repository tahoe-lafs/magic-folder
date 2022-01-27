import json
import os.path

from eliot.twisted import inline_callbacks

from testtools import (
    ExpectedException,
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
from ...status import (
    WebSocketStatusService,
)
from ...endpoints import (
    CannotConvertEndpointError,
)
from ...service import MagicFolderService
from ...snapshot import (
    create_local_author,
)
from ...tahoe_client import (
    create_tahoe_client,
)

from ...cli import MagicFolderCommand
from ...common import (
    InvalidMagicFolderName,
)
from ...util.capabilities import (
    Capability,
)
from ..common import (
    AsyncTestCase,
    SyncTestCase,
)
from ..fixtures import (
    NodeDirectory,
)
from .common import (
    cli,
)
from ...testing.web import create_tahoe_treq_client, create_fake_tahoe_root


def parse_cli(*argv):
    # This parses the CLI options (synchronously), and returns the Options
    # argument, or throws usage.UsageError if something went wrong.
    options = MagicFolderCommand()
    options.parseOptions(argv)
    return options


class ListMagicFolder(AsyncTestCase):
    """
    Tests for the command-line interface ``magic-folder list``.
    """

    def cli(self, argv):
        return cli(argv, self.config, self.http_client)

    @inline_callbacks
    def setUp(self):
        """
        Create a Tahoe-LAFS node which can contain some magic folder configuration
        and run it.
        """
        yield super(ListMagicFolder, self).setUp()

        # for these tests we never contact Tahoe so we can get
        # away with an "empty" Tahoe WebUI
        tahoe_client = create_tahoe_client(DecodedURL.from_text(u""), StubTreq(Resource())),
        self.config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath(u"/no/tahoe/node-directory"),
        )
        status_service = WebSocketStatusService(reactor, self.config)
        global_service = MagicFolderService(
            reactor, self.config, status_service, tahoe_client,
        )
        self.http_client = create_testing_http_client(
            reactor,
            self.config,
            global_service,
            lambda: self.config.api_token,
            status_service,
        )

    @inline_callbacks
    def test_list_none(self):
        """
        When there are no Magic Folders at all, the output of the list command
        reports this.
        """
        outcome = yield self.cli(
            [u"list"],
        )
        self.assertThat(outcome.stdout, Contains(u"No magic-folders"))

    @inline_callbacks
    def test_list_none_json(self):
        """
        When there are no Magic Folders at all, the output of the list command
        reports this in JSON format if given ``--json``.
        """
        outcome = yield self.cli(
            [u"list", u"--json"],
        )
        self.assertThat(outcome.stdout, AfterPreprocessing(json.loads, Equals({})))

    @inline_callbacks
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
            create_local_author(u"alice"),
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            1,
            None,
        )

        outcome = yield self.cli([u"list"])
        self.expectThat(outcome.stdout, Contains(u"list-some-folder"))
        self.expectThat(outcome.stdout, Contains(folder_path.path))

    @inline_callbacks
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
            create_local_author(u"alice"),
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            1,
            60,
        )

        outcome = yield self.cli(
            [u"list", u"--json", u"--include-secret-information"],
        )
        self.assertThat(
            outcome.stdout,
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


class CreateMagicFolder(AsyncTestCase):

    def cli(self, argv):
        return cli(argv, self.config, self.http_client)

    @inline_callbacks
    def setUp(self):
        """
        Create a Tahoe-LAFS node which can contain some magic folder configuration
        and run it.
        """
        yield super(CreateMagicFolder, self).setUp()

        self.root = create_fake_tahoe_root()
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(self.root),
        )

        self.config_dir = FilePath(self.mktemp())
        self.config = create_testing_configuration(
            self.config_dir,
            FilePath(u"/non-tahoe-directory"),
        )
        status_service = WebSocketStatusService(reactor, self.config)
        folder_service = MagicFolderService(
            reactor, self.config, status_service, tahoe_client,
        )
        self.http_client = create_testing_http_client(
            reactor,
            self.config,
            folder_service,
            lambda: self.config.api_token,
            status_service,
        )

    @inline_callbacks
    def test_add_magic_folder(self):
        """
        Create a new magic folder with a nickname and local directory so
        that this folder is also invited and joined with the given nickname.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"test",
                u"--author", u"test",
                magic_folder.path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @inline_callbacks
    def test_add_magic_folder_intervals(self):
        """
        Create a new magic folder with a nickname and local directory so
        that this folder is also invited and joined with the given nickname.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"test",
                u"--author", u"test",
                u"--poll-interval", u"30",
                u"--scan-interval", u"30",
                magic_folder.path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )
        folder_config = self.config.get_magic_folder("test")
        self.assertThat(
            (folder_config.poll_interval, folder_config.scan_interval),
            Equals((30, 30)),
        )

    @inline_callbacks
    def test_add_magic_folder_disable_scanning(self):
        """
        Create a new magic folder with a nickname and local directory so
        that this folder is also invited and joined with the given nickname.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"test",
                u"--author", u"test",
                u"--disable-scanning",
                magic_folder.path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )
        folder_config = self.config.get_magic_folder("test")
        self.assertThat(
            folder_config.scan_interval,
            Equals(None),
        )

    @inline_callbacks
    def test_create_duplicate_name(self):
        """
        Create a magic folder and if that succeeds, then create another
        magic folder with the same name and check if this results in an
        error.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"foo",
                u"--author", u"test",
                magic_folder.path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Always(),
        )

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"foo",
                u"--author", u"test",
                magic_folder.path,
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

    @inline_callbacks
    def test_create_invalid_name(self):
        """
        `magic-folder add` reports invalid folder names.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"/",
                u"--author", u"test",
                magic_folder.path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            InvalidMagicFolderName.message,
            outcome.stderr
        )

    @inline_callbacks
    def test_add_leave_folder(self):
        """
        Create a magic folder and then leave the folder and check
        whether it was successful.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"foo",
                u"--author", u"test",
                magic_folder.path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
            str(outcome),
        )

        outcome = yield self.cli(
            [
                u"leave",
                u"--name", u"foo",
                u"--really-delete-write-capability",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @inline_callbacks
    def test_leave_wrong_folder(self):
        """
        Create a magic folder with a specified name and then invoke
        the leave command with a different specified name. This should
        result in a failure.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--author", u"test",
                u"--name", u"foo",
                magic_folder.path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield self.cli(
            [
                u"leave",
                u"--name", u"bar",
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

    @inline_callbacks
    def test_leave_no_folder(self):
        """
        Create a magic folder and then leave the folder. Leaving it again
        should result in an error.
        """
        # Get a magic folder.
        magic_folder = FilePath(self.mktemp())
        magic_folder.makedirs()

        outcome = yield self.cli(
            [
                u"add",
                u"--name", u"foo",
                u"--author", u"alice",
                magic_folder.path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield self.cli(
            [
                u"leave",
                u"--name", u"foo",
                u"--really-delete-write-capability",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield self.cli(
            [
                u"leave",
                u"--name", u"foo",
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

    @inline_callbacks
    def test_leave_no_folders_at_all(self):
        """
        Leave a non-existant magic folder. This should result in
        an error.
        """
        outcome = yield self.cli(
            [
                u"leave",
                u"--name", u"foo",
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


class ConfigOptionTests(SyncTestCase):
    def test_help_synopsis(self):
        """
        Test if synonsis is defined for the help switch.
        """
        self.basedir = "cli/MagicFolder/help_synopsis"
        os.makedirs(self.basedir)

        o = magic_folder_cli.AddOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent.getSynopsis()

    @inline_callbacks
    def test_config_directory_is_file(self):
        """
        Using --config with a file is an error
        """
        confdir = FilePath(self.mktemp())
        with confdir.open("wb") as f:
            f.write(b"dummy\n")

        outcome = yield cli(["--config", confdir.path, "list"])
        self.assertThat(outcome.code, Equals(1))
        self.assertThat(outcome.stderr, Contains("Unable to load configuration"))

    @inline_callbacks
    def test_config_directory_empty(self):
        """
        A directory that is empty isn't valid for --config
        """
        confdir = FilePath(self.mktemp())
        confdir.makedirs()

        outcome = yield cli(["--config", confdir.path, "list"])
        self.assertThat(outcome.code, Equals(1))
        self.assertThat(outcome.stderr, Contains("Unable to load configuration"))

    @inline_callbacks
    def test_config_directory(self):
        """
        Passing --config option loads the configuration from the provided directory.
        """
        confdir = FilePath(self.mktemp())
        nodedir = self.useFixture(
            NodeDirectory(FilePath(self.mktemp()))
        )
        yield magic_folder_initialize(confdir, u"tcp:5555", nodedir.path, None)

        options = magic_folder_cli.MagicFolderCommand()
        options.parseOptions(["--config", confdir.path, "list"])
        self.assertThat(
            options.config,
            MatchesStructure(
                basedir=Equals(confdir),
                api_endpoint=Equals(u"tcp:5555"),
                tahoe_node_directory=Equals(nodedir.path),
            )
        )

    @inline_callbacks
    def test_default_config_directory(self):
        """
        Not passing a --config loads the configuration from the default directory.
        """
        confdir = FilePath(self.mktemp())
        nodedir = self.useFixture(
            NodeDirectory(FilePath(self.mktemp()))
        )
        yield magic_folder_initialize(confdir, u"tcp:5555", nodedir.path, None)

        options = magic_folder_cli.MagicFolderCommand()
        # twisted.python.usage.Options use .opts to store
        # the defaults before parsing. We override that
        # here to test that parsing a command without
        # --config picks up that default.
        options.opts["config"] = confdir.path
        options.parseOptions(["list"])
        self.assertThat(
            options.config,
            MatchesStructure(
                basedir=Equals(confdir),
            )
        )


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

    def test_poll_interval_negative(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli(
                "add",
                "--name", "test",
                "--author", "test",
                "--poll-interval=-1",
                self.temp.path
            )
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

    def test_poll_interval_zero(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli(
                "add",
                "--name", "test",
                "--author", "test",
                "--poll-interval=0",
                self.temp.path
            )
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

    def test_scan_interval(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli(
                "add",
                "--name", "test",
                "--author", "test",
                "--scan-interval=frog",
                self.temp.path
            )
        self.assertEqual(str(ctx.exception), "--scan-interval must be a positive integer")

    def test_scan_interval_negative(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli(
                "add",
                "--name", "test",
                "--author", "test",
                "--scan-interval=-1",
                self.temp.path
            )
        self.assertEqual(str(ctx.exception), "--scan-interval must be a positive integer")

    def test_scan_interval_zero(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli(
                "add",
                "--name", "test",
                "--author", "test",
                "--scan-interval=0",
                self.temp.path
            )
        self.assertEqual(str(ctx.exception), "--scan-interval must be a positive integer")


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
