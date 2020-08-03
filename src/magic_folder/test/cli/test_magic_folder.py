import json
import os.path

from testtools.content import (
    text_content,
)
from testtools.matchers import (
    Contains,
    Equals,
    AfterPreprocessing,
)

from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import usage

from ... import cli as magic_folder_cli

from ..common_util import (
    parse_cli,
)
from ..common import (
    AsyncTestCase,
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

    @defer.inlineCallbacks
    def test_list_none(self):
        """
        When there are no Magic Folders at all, the output of the list command
        reports this.
        """
        outcome = yield cli(
            self.node_directory,
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
            self.node_directory,
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
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"--name", b"list-some-folder",
                b"magik:",
                b"test_list_some",
                folder_path.asBytesMode().path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.node_directory,
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
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"--name", b"list-some-json-folder",
                b"magik:",
                b"test_list_some_json",
                folder_path.asBytesMode().path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )
        outcome = yield cli(
            self.node_directory,
            [b"list", b"--json"],
        )
        self.expectThat(
            outcome.stdout,
            AfterPreprocessing(
                json.loads,
                Equals({
                    u"list-some-json-folder": {
                        u"directory": folder_path.path,
                    },
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
        self.node_directory = self.client_fixture.node_directory

    @defer.inlineCallbacks
    def test_create_magic_folder(self):
        """
        Create a new magic folder with a nickname and local directory so
        that this folder is also invited and joined with the given nickname.
        """
        # Get a magic folder.
        magic_folder = self.tempdir.child(u"magic-folder")
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"magik:",
                b"test_create",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @defer.inlineCallbacks
    def test_create_error(self):
        """
        Try to create a magic folder with an invalid nickname and check if
        this results in an error.
        """
        magic_folder = self.tempdir.child(u"magic-folder")
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"m a g i k",
                b"test_create_error",
                magic_folder.asBytesMode().path,
            ]
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
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
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"--name",
                b"foo",
                b"magik:",
                b"test_create_duplicate",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"--name",
                b"foo",
                b"magik:",
                b"test_create_duplicate",
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
    def test_create_leave_folder(self):
        """
        Create a magic folder and then leave the folder and check
        whether it was successful.
        """
        # Get a magic folder.
        magic_folder = self.tempdir.child(u"magic-folder")
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"--name",
                b"foo",
                b"magik:",
                b"test_create_leave_folder",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.node_directory, [
                b"leave",
                b"--name",
                b"foo",
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
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"--name",
                b"foo",
                b"magik:",
                b"test_create_leave_folder",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.node_directory, [
                b"leave",
                b"--name",
                b"bar",
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
        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"--name",
                b"foo",
                b"magik:",
                b"test_create_leave_folder",
                magic_folder.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.node_directory, [
                b"leave",
                b"--name",
                b"foo",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        outcome = yield cli(
            self.node_directory, [
                b"leave",
                b"--name",
                b"foo",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "No magic-folders at all",
            outcome.stderr
        )

    @defer.inlineCallbacks
    def test_leave_no_folders_at_all(self):
        """
        Leave a non-existant magic folder. This should result in
        an error.
        """
        outcome = yield cli(
            self.node_directory, [
                b"leave",
                b"--name",
                b"foo",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.assertIn(
            "No magic-folders at all",
            outcome.stderr
        )

    @defer.inlineCallbacks
    def test_create_invite_join(self):
        """
        Create a magic folder, create an invite code and use the
        code to join.
        """
        # Get a magic folder.
        basedir = self.tempdir.child(u"magic-folder")
        local_dir = basedir.child(u"alice")
        local_dir.makedirs()

        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"magik:",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # create invite code for alice
        outcome = yield cli(
            self.node_directory, [
                b"invite",
                b"magik:",
                b"bob",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # capture the invite code from stdout
        invite_code = outcome.stdout.strip()

        # create a directory for Bob
        mf_bob = basedir.child(u"bob")
        mf_bob.makedirs()
        # join
        outcome = yield cli(
            self.node_directory, [
                b"join",
                b"--author", b"test-dummy",
                invite_code,
                mf_bob.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @defer.inlineCallbacks
    def test_join_leave_join(self):
        """
        Create a magic folder, create an invite code, use the
        code to join, leave the folder and then join again with
        the same invite code.
        """
        # Get a magic folder.
        basedir = self.tempdir.child(u"magic-folder")

        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"magik:",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # create invite code for bob
        outcome = yield cli(
            self.node_directory, [
                b"invite",
                b"magik:",
                b"bob",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # capture the invite code from stdout
        invite_code = outcome.stdout.strip()

        # create a directory for Bob
        mf_bob = basedir.child(u"bob")
        mf_bob.makedirs()

        # join
        outcome = yield cli(
            self.node_directory, [
                b"join",
                b"--author", b"test-dummy",
                invite_code,
                mf_bob.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # leave
        outcome = yield cli(
            self.node_directory, [
                b"leave",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # join
        outcome = yield cli(
            self.node_directory, [
                b"join",
                b"--author", b"test-dummy",
                invite_code,
                mf_bob.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    def test_help_synopsis(self):
        """
        Test if synonsis is defined for the help switch.
        """
        self.basedir = "cli/MagicFolder/help_synopsis"
        os.makedirs(self.basedir)

        o = magic_folder_cli.CreateOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent.getSynopsis()

    def test_no_node_directory(self):
        """
        Running a command without --node-directory fails
        """
        o = magic_folder_cli.InviteOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()

        try:
            o.parseOptions(["alias:", "nickname"])
        except usage.UsageError as e:
            self.assertIn("Must supply --node-directory", str(e))
        else:
            self.fail("expected UsageError")

    def test_node_directory_is_file(self):
        """
        Using --node-directory with a file is an error
        """
        o = magic_folder_cli.MagicFolderCommand()
        nodefile = self.mktemp()
        with open(nodefile, "w") as f:
            f.write("dummy\n")

        try:
            o.parseOptions(["--node-directory", nodefile, "invite", "alias:", "nickname"])
        except usage.UsageError as e:
            self.assertIn("is not a directory", str(e))
        else:
            self.fail("expected UsageError")

    def test_node_directory_empty(self):
        """
        A directory that is empty isn't valid for --node-directory
        """
        o = magic_folder_cli.MagicFolderCommand()
        nodedir = self.mktemp()
        os.mkdir(nodedir)

        try:
            o.parseOptions(["--node-directory", nodedir, "invite", "alias:", "nickname"])
        except usage.UsageError as e:
            self.assertIn("doesn't look like a Tahoe directory", str(e))
        else:
            self.fail("expected UsageError")

    def test_create_invite_join_failure(self):
        """
        Test the cli input for valid local directory name.
        """
        self.basedir = "cli/MagicFolder/create-invite-join-failure"
        os.makedirs(self.basedir)

        o = magic_folder_cli.CreateOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent['node-directory'] = self.basedir
        try:
            o.parseArgs("magic:", "Alice", "-foo")
        except usage.UsageError as e:
            self.assertIn("cannot start with '-'", str(e))
        else:
            self.fail("expected UsageError")

    def test_join_failure(self):
        """
        Test the cli input for valid invite code.
        """
        self.basedir = "cli/MagicFolder/create-join-failure"
        os.makedirs(self.basedir)

        o = magic_folder_cli.JoinOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent['node-directory'] = self.basedir
        try:
            o.parseArgs("URI:invite+URI:code", "-foo")
        except usage.UsageError as e:
            self.assertIn("cannot start with '-'", str(e))
        else:
            self.fail("expected UsageError")

    @defer.inlineCallbacks
    def test_join_author_user(self):
        """
        The CLI will use USER from the environment
        """
        basedir = self.tempdir.child(u"join-author-user")
        basedir.makedirs()

        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"join_author_user:",
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )
        # create invite code for bob
        outcome = yield cli(
            self.node_directory, [
                b"invite",
                b"join_author_user:",
                b"bob",
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # capture the invite code from stdout
        invite_code = outcome.stdout.strip()

        # create a directory for Bob
        mf_bob = basedir.child(u"bob")
        mf_bob.makedirs()

        # join
        # we don't pass --author so should get author from $USER
        olduser = os.environ.get("USER", None)
        os.environ["USER"] = "bob_from_user"
        try:
            outcome = yield cli(
                self.node_directory, [
                    b"join",
                    # no --author, so it should come from USER env-var
                    invite_code,
                    mf_bob.asBytesMode().path,
                ],
            )
        finally:
            if olduser is None:
                del os.environ["USER"]
            else:
                os.environ["USER"] = olduser

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )


    @defer.inlineCallbacks
    def test_join_twice_failure(self):
        """
        Create a magic folder, create an invite code, use it to join and then
        join again with the same code without leaving. This should result
        in an error.
        """
        # Get a magic folder.
        basedir = self.tempdir.child(u"magic-folder")
        local_dir = basedir.child(u"alice")
        local_dir.makedirs()

        outcome = yield cli(
            self.node_directory, [
                b"create",
                b"magik:",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # create invite code for alice
        outcome = yield cli(
            self.node_directory, [
                b"invite",
                b"magik:",
                b"bob",
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # capture the invite code from stdout
        invite_code = outcome.stdout.strip()

        # create a directory for Bob
        mf_bob = basedir.child(u"bob")
        mf_bob.makedirs()

        # join
        outcome = yield cli(
            self.node_directory, [
                b"join",
                b"--author", b"test-dummy",
                invite_code,
                mf_bob.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        # join
        outcome = yield cli(
            self.node_directory, [
                b"join",
                b"--author", b"test-dummy",
                invite_code,
                mf_bob.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(False),
        )

        self.assertIn(
            outcome.stderr,
            "This client already has a magic-folder named 'default'\n"
        )

class CreateErrors(AsyncTestCase):
    def test_poll_interval(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("create", "--poll-interval=frog", "alias:")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("create", "--poll-interval=-4", "alias:")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

    def test_alias(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("create", "no-colon")
        self.assertEqual(str(ctx.exception), "An alias must end with a ':' character.")

    def test_nickname(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("create", "alias:", "nickname")
        self.assertEqual(str(ctx.exception), "If NICKNAME is specified then LOCAL_DIR must also be specified.")


class InviteErrors(AsyncTestCase):
    def test_alias(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("invite", "no-colon")
        self.assertEqual(str(ctx.exception), "An alias must end with a ':' character.")

class JoinErrors(AsyncTestCase):
    def test_poll_interval(self):
        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("join", "--author", "test-dummy", "--poll-interval=frog", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("join", "--author", "test-dummy", "--poll-interval=-2", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")
