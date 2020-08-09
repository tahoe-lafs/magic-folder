import json
import os.path

from testtools.content import (
    text_content,
)
from testtools.matchers import (
    Contains,
    Equals,
    AfterPreprocessing,
    Always,
    ContainsDict,
)

from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import usage
from twisted.python.filepath import (
    FilePath,
)

from ... import cli as magic_folder_cli
from ...config import (
    create_global_configuration,
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
        self.config_dir = FilePath(self.mktemp())
        create_global_configuration(self.config_dir, u"tcp:4321", self.node_directory)

    @defer.inlineCallbacks
    def test_list_none(self):
        """
        When there are no Magic Folders at all, the output of the list command
        reports this.
        """
        outcome = yield cli(
            self.config_dir,
            [b"list"],
        )
        self.assertThat(outcome.stdout, Contains(u"No magic-folders"))

    @defer.inlineCallbacks
    def test_list_none_json(self):
        """
        When there are no Magic Folders at all, the output of the list command
        reports this in JSON format if given ``--json``.
        """
        outcome = yield cli(
            self.config_dir,
            [b"list", b"--json"],
        )
        self.assertThat(outcome.stdout, AfterPreprocessing(json.loads, Equals({})))

    @defer.inlineCallbacks
    def test_list_some(self):
        """
        When there are Magic Folders, the output of the list command describes
        them.
        """
        # Get a magic folder.
        folder_path = self.tempdir.child(u"magic-folder")
        folder_path.makedirs()

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--name", b"list-some-folder",
                b"--author", b"alice",
                folder_path.asBytesMode().path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.config_dir,
            [b"list"],
        )
        self.expectThat(outcome.stdout, Contains(b"list-some-folder"))
        self.expectThat(outcome.stdout, Contains(folder_path.path))

    @defer.inlineCallbacks
    def test_list_some_json(self):
        """
        When there are Magic Folders, the output of the list command describes
        them in JSON format if given ``--json``.
        """
        # Get a magic folder.
        folder_path = self.tempdir.child(u"magic-folder")
        folder_path.makedirs()

        outcome = yield cli(
            self.config_dir, [
                b"add",
                b"--author", b"test",
                b"--name", b"list-some-json-folder",
                folder_path.asBytesMode().path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )
        outcome = yield cli(
            self.config_dir,
            [b"list", b"--json", b"--include-secret-information"],
        )
        self.expectThat(
            outcome.stdout,
            AfterPreprocessing(
                json.loads,
                ContainsDict({
                    u"list-some-json-folder": ContainsDict({
                        u"magic_path": Equals(folder_path.path),
                        u"poll_interval": Equals(60),
                        u"is_admin": Equals(True),
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


class JoinErrors(AsyncTestCase):
    def test_poll_interval(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("join", "--author", "test-dummy", "--poll-interval=frog", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("join", "--author", "test-dummy", "--poll-interval=-2", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")
