import json
import os.path
import re

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    datetimes,
    dictionaries,
    lists,
    tuples,
    just,
)

from testtools.content import (
    text_content,
)
from testtools.matchers import (
    Contains,
    Equals,
    AfterPreprocessing,
    IsInstance,
)

from eliot import (
    log_call,
    start_action,
)
from eliot.twisted import (
    DeferredContext,
)
from twisted.internet import defer
from twisted.internet import reactor
from twisted.python import usage
from twisted.python.filepath import (
    FilePath,
)

from allmydata.util.assertutil import precondition
from allmydata.util import fileutil
from allmydata.scripts.common import get_aliases
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import unicode_to_argv
from allmydata import uri
from allmydata.util.eliotutil import (
    log_call_deferred,
)

from ...frontends.magic_folder import (
    MagicFolder,
)
from ...scripts import (
    magic_folder_cli,
)
from ...web.magic_folder import (
    status_for_item,
)
from ...status import (
    Status,
)

from ..common_util import (
    parse_cli,
    NonASCIIPathMixin,
)
from ..common import (
    AsyncTestCase,
    SameProcessStreamEndpointAssigner,
)
from ..fixtures import (
    SelfConnectedClient,
)
from ..strategies import (
    folder_names,
    queued_items,
    path_segments,
    filenodes,
)
from .common import (
    CLITestMixin,
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


class StatusMagicFolder(AsyncTestCase):
    """
    Tests for ``magic-folder status``.
    """
    @defer.inlineCallbacks
    def test_command_exists(self):
        """
        There is a status command at all.
        """
        outcome = yield cli(
            FilePath(self.mktemp()),
            [b"status", b"--help"],
        )
        addOutcomeDetails(self, outcome)
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @defer.inlineCallbacks
    def test_command_error(self):
        """
        If the status command encounters an error it reports it on stderr and
        exits with a non-zero code.
        """
        outcome = yield cli(
            # Pass in a fanciful node directory to provoke a predictable
            # error.
            FilePath(self.mktemp()),
            [b"status"],
        )
        self.expectThat(
            outcome.succeeded(),
            Equals(False),
        )
        self.expectThat(
            outcome.stderr,
            Contains(b"No such file or directory"),
        )

    @defer.inlineCallbacks
    def test_command_success(self):
        """
        If the status command succeeds it reports some information on stdout.
        """
        client_fixture = SelfConnectedClient(reactor)
        yield client_fixture.use_on(self)

        # Create a magic folder so that we can inspect its status.
        magic_folder = client_fixture.tempdir.child(u"magic-folder")
        outcome = yield cli(
            client_fixture.node_directory,
            [b"create",
             b"magic-folder-alias:",
             b"member-alias",
             magic_folder.asBytesMode().path,
            ],
        )
        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

        assigner = SameProcessStreamEndpointAssigner()
        assigner.setUp()
        self.addCleanup(assigner.tearDown)
        ignored, endpoint_description = assigner.assign(reactor)

        # Start the magic folder service after creating the magic folder so it
        # will be noticed.
        magic_folder_service = magic_folder_cli.MagicFolderService.from_node_directory(
            reactor,
            client_fixture.node_directory.path,
            endpoint_description,
        )
        magic_folder_service.startService()
        self.addCleanup(magic_folder_service.stopService)

        outcome = yield cli(
            client_fixture.node_directory,
            [b"status"],
        )

        addOutcomeDetails(self, outcome)

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    @given(
        folder_names(),
        datetimes(),
        dictionaries(
            path_segments(),
            tuples(just(u"filenode"), filenodes()),
        ),
        # Laziness
        path_segments(),
        lists(queued_items()),
        lists(queued_items()),
    )
    def test_formatting(
            self,
            folder_name,
            now,
            local_files,
            remote_name,
            upload_items,
            download_items,
    ):
        self.assertThat(
            magic_folder_cli._format_status(
                now,
                Status(
                    folder_name,
                    local_files=local_files,
                    remote_files={remote_name: local_files},
                    folder_status=list(
                        status_for_item(kind, item)
                        for (kind, items) in [
                                ("upload", upload_items),
                                ("download", download_items),
                        ]
                        for item in items
                    ),
                ),
            ),
            IsInstance(unicode),
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
        # Get a magic folder.
        basedir = self.tempdir.child(u"magic-folder")
        local_dir = basedir.child(u"alice")
        local_dir.makedirs()
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

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
        # Get a magic folder.
        basedir = self.tempdir.child(u"magic-folder")
        local_dir = basedir.child(u"alice")
        local_dir.makedirs()
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

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
                invite_code,
                mf_bob.asBytesMode().path,
            ],
        )

        self.assertThat(
            outcome.succeeded(),
            Equals(True),
        )

    def test_join_failure(self):
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

    def test_help_synopsis(self):
        self.basedir = "cli/MagicFolder/help_synopsis"
        os.makedirs(self.basedir)

        o = magic_folder_cli.CreateOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent.getSynopsis()

    def test_create_invite_join_failure(self):
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
    def test_join_twice_failure(self):
        # Get a magic folder.
        basedir = self.tempdir.child(u"magic-folder")
        local_dir = basedir.child(u"alice")
        local_dir.makedirs()
        abs_local_dir_u = abspath_expanduser_unicode(unicode(local_dir), long_path=False)

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
            parse_cli("join", "--poll-interval=frog", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")

        with self.assertRaises(usage.UsageError) as ctx:
            parse_cli("join", "--poll-interval=-2", "code", "localdir")
        self.assertEqual(str(ctx.exception), "--poll-interval must be a positive integer")
