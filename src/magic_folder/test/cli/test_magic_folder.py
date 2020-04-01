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

from ...magic_folder import (
    MagicFolder,
    load_magic_folders,
)
from ... import cli as magic_folder_cli

from ...web.magic_folder import (
    status_for_item,
)
from ...status import (
    Status,
)

from ..no_network import GridTestMixin
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

class MagicFolderCLITestMixin(CLITestMixin, GridTestMixin, NonASCIIPathMixin):
    def setUp(self):
        GridTestMixin.setUp(self)
        self.alice_nickname = self.unicode_or_fallback(u"Alice\u00F8", u"Alice", io_as_well=True)
        self.bob_nickname = self.unicode_or_fallback(u"Bob\u00F8", u"Bob", io_as_well=True)

    def do_create_magic_folder(self, client_num):
        with start_action(action_type=u"create-magic-folder", client_num=client_num).context():
            d = DeferredContext(
                self.do_cli(
                    "magic-folder", "--debug", "create", "magic:",
                    client_num=client_num,
                )
            )
        def _done(args):
            (rc, stdout, stderr) = args
            self.assertEqual(rc, 0, stdout + stderr)
            self.assertIn("Alias 'magic' created", stdout)
            self.assertEqual(stderr, "")
            aliases = get_aliases(self.get_clientdir(i=client_num))
            self.assertIn("magic", aliases)
            self.assertTrue(aliases["magic"].startswith("URI:DIR2:"))
        d.addCallback(_done)
        return d.addActionFinish()

    def do_invite(self, client_num, nickname):
        nickname_arg = unicode_to_argv(nickname)
        action = start_action(
            action_type=u"invite-to-magic-folder",
            client_num=client_num,
            nickname=nickname,
        )
        with action.context():
            d = DeferredContext(
                self.do_cli(
                    "magic-folder",
                    "invite",
                    "magic:",
                    nickname_arg,
                    client_num=client_num,
                )
            )
        def _done(args):
            (rc, stdout, stderr) = args
            self.assertEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d.addActionFinish()

    def do_list(self, client_num, json=False):
        args = ("magic-folder", "list",)
        if json:
            args = args + ("--json",)
        d = self.do_cli(*args, client_num=client_num)
        def _done(args):
            (rc, stdout, stderr) = args
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def do_status(self, client_num, name=None):
        args = ("magic-folder", "status",)
        if name is not None:
            args = args + ("--name", name)
        d = self.do_cli(*args, client_num=client_num)
        def _done(args):
            (rc, stdout, stderr) = args
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def do_join(self, client_num, local_dir, invite_code):
        action = start_action(
            action_type=u"join-magic-folder",
            client_num=client_num,
            local_dir=local_dir,
            invite_code=invite_code,
        )
        with action.context():
            precondition(isinstance(local_dir, unicode), local_dir=local_dir)
            precondition(isinstance(invite_code, str), invite_code=invite_code)
            local_dir_arg = unicode_to_argv(local_dir)
            d = DeferredContext(
                self.do_cli(
                    "magic-folder",
                    "join",
                    invite_code,
                    local_dir_arg,
                    client_num=client_num,
                )
            )
        def _done(args):
            (rc, stdout, stderr) = args
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d.addActionFinish()

    def do_leave(self, client_num):
        d = self.do_cli("magic-folder", "leave", client_num=client_num)
        def _done(args):
            (rc, stdout, stderr) = args
            self.assertEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        return d

    def check_joined_config(self, client_num, upload_dircap):
        """Tests that our collective directory has the readonly cap of
        our upload directory.
        """
        action = start_action(action_type=u"check-joined-config")
        with action.context():
            collective_readonly_cap = self.get_caps_from_files(client_num)[0]
            d = DeferredContext(
                self.do_cli(
                    "ls", "--json",
                    collective_readonly_cap,
                    client_num=client_num,
                )
            )
        def _done(args):
            (rc, stdout, stderr) = args
            self.assertEqual(rc, 0)
            return (rc, stdout, stderr)
        d.addCallback(_done)
        def test_joined_magic_folder(args):
            (rc, stdout, stderr) = args
            readonly_cap = unicode(uri.from_string(upload_dircap).get_readonly().to_string(), 'utf-8')
            s = re.search(readonly_cap, stdout)
            self.assertTrue(s is not None)
            return None
        d.addCallback(test_joined_magic_folder)
        return d.addActionFinish()

    def get_caps_from_files(self, client_num):
        folders = load_magic_folders(self.get_clientdir(i=client_num))
        mf = folders["default"]
        return mf['collective_dircap'], mf['upload_dircap']

    @log_call
    def check_config(self, client_num, local_dir):
        client_config = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "tahoe.cfg"))
        mf_yaml = fileutil.read(os.path.join(self.get_clientdir(i=client_num), "private", "magic_folders.yaml"))
        local_dir_utf8 = local_dir.encode('utf-8')
        magic_folder_config = "[magic_folder]\nenabled = True"
        self.assertIn(magic_folder_config, client_config)
        self.assertIn(local_dir_utf8, mf_yaml)

    def create_invite_join_magic_folder(self, nickname, local_dir):
        nickname_arg = unicode_to_argv(nickname)
        local_dir_arg = unicode_to_argv(local_dir)
        # the --debug means we get real exceptions on failures
        d = self.do_cli("magic-folder", "--debug", "create", "magic:", nickname_arg, local_dir_arg)
        def _done(args):
            (rc, stdout, stderr) = args
            self.assertEqual(rc, 0, stdout + stderr)

            client = self.get_client()
            self.collective_dircap, self.upload_dircap = self.get_caps_from_files(0)
            self.collective_dirnode = client.create_node_from_uri(self.collective_dircap)
            self.upload_dirnode     = client.create_node_from_uri(self.upload_dircap)
        d.addCallback(_done)
        d.addCallback(lambda ign: self.check_joined_config(0, self.upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, local_dir))
        return d

    # XXX should probably just be "tearDown"...
    @log_call_deferred(action_type=u"test:cli:magic-folder:cleanup")
    def cleanup(self, res):
        d = DeferredContext(defer.succeed(None))
        def _clean(ign):
            return self.magicfolder.disownServiceParent()

        d.addCallback(_clean)
        d.addCallback(lambda ign: res)
        return d.result

    def init_magicfolder(self, client_num, upload_dircap, collective_dircap, local_magic_dir, clock):
        dbfile = abspath_expanduser_unicode(u"magicfolder_default.sqlite", base=self.get_clientdir(i=client_num))
        magicfolder = MagicFolder(
            client=self.get_client(client_num),
            upload_dircap=upload_dircap,
            collective_dircap=collective_dircap,
            local_path_u=local_magic_dir,
            dbfile=dbfile,
            umask=0o077,
            name='default',
            clock=clock,
            uploader_delay=0.2,
            downloader_delay=0,
        )

        magicfolder.setServiceParent(self.get_client(client_num))
        magicfolder.ready()
        return magicfolder

    def setup_alice_and_bob(self, alice_clock=reactor, bob_clock=reactor):
        self.set_up_grid(num_clients=2, oneshare=True)

        self.alice_magicfolder = None
        self.bob_magicfolder = None

        alice_magic_dir = abspath_expanduser_unicode(u"Alice-magic", base=self.basedir)
        self.mkdir_nonascii(alice_magic_dir)
        bob_magic_dir = abspath_expanduser_unicode(u"Bob-magic", base=self.basedir)
        self.mkdir_nonascii(bob_magic_dir)

        # Alice creates a Magic Folder, invites herself and joins.
        d = self.do_create_magic_folder(0)
        d.addCallback(lambda ign: self.do_invite(0, self.alice_nickname))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(0, alice_magic_dir, self.invite_code))
        def get_alice_caps(ign):
            self.alice_collective_dircap, self.alice_upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_alice_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.alice_upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, alice_magic_dir))
        def get_Alice_magicfolder(result):
            self.alice_magicfolder = self.init_magicfolder(0, self.alice_upload_dircap,
                                                           self.alice_collective_dircap,
                                                           alice_magic_dir, alice_clock)
            return result
        d.addCallback(get_Alice_magicfolder)

        # Alice invites Bob. Bob joins.
        d.addCallback(lambda ign: self.do_invite(0, self.bob_nickname))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(1, bob_magic_dir, self.invite_code))
        def get_bob_caps(ign):
            self.bob_collective_dircap, self.bob_upload_dircap = self.get_caps_from_files(1)
        d.addCallback(get_bob_caps)
        d.addCallback(lambda ign: self.check_joined_config(1, self.bob_upload_dircap))
        d.addCallback(lambda ign: self.check_config(1, bob_magic_dir))
        def get_Bob_magicfolder(result):
            self.bob_magicfolder = self.init_magicfolder(1, self.bob_upload_dircap,
                                                         self.bob_collective_dircap,
                                                         bob_magic_dir, bob_clock)
            return result
        d.addCallback(get_Bob_magicfolder)
        return d


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

    def test_help_synopsis(self):
        """
        Test if synonsis is defined for the help switch.
        """
        self.basedir = "cli/MagicFolder/help_synopsis"
        os.makedirs(self.basedir)

        o = magic_folder_cli.CreateOptions()
        o.parent = magic_folder_cli.MagicFolderCommand()
        o.parent.getSynopsis()

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
