import json
from io import StringIO

from eliot.twisted import (
    inline_callbacks,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.usage import (
    UsageError,
)
from twisted.internet.task import (
    Clock,
)
from hyperlink import (
    DecodedURL,
)
from testtools.matchers import (
    AfterPreprocessing,
    Equals,
    StartsWith,
    Contains,
    IsInstance,
)
from testtools.twistedsupport import (
    failed,
)
from treq.testing import (
    RequestSequence,
    StringStubbingResource,
    StubTreq,
)
from autobahn.twisted.testing import (
    create_memory_agent,
    MemoryReactorClockResolver,
    create_pumper,
)
from nacl.encoding import (
    HexEncoder,
)

from ..client import (
    create_magic_folder_client,
    MagicFolderClient,
    CannotAccessAPIError,
)
from ..api_cli import (
    dispatch_magic_folder_api_command,
    run_magic_folder_api_options,
    MagicFolderApiCommand,
)
from ..config import (
    create_testing_configuration,
    create_global_configuration,
    GlobalConfigDatabase,
)
from ..status import (
    StatusFactory,
    WebSocketStatusService,
)
from ..snapshot import (
    create_local_author,
    LocalSnapshot,
    RemoteSnapshot,
)
from ..util.file import (
    PathState,
    get_pathinfo,
)
from ..util.capabilities import (
    Capability,
    random_immutable,
)
from .common import (
    AsyncTestCase,
)


class TestApiAddSnapshot(AsyncTestCase):
    """
    Tests related to 'magic-folder-api add-snapshot'

    Since the aim of these is to confirm operation of the command
    itself most tests use treq testing features to confirm that the
    correct HTTP API is invoked .. (other tests confirm the correct
    operation of the APIs themselves).
    """
    url = DecodedURL.from_text(u"http://invalid./v1/")

    def setUp(self):
        super(TestApiAddSnapshot, self).setUp()
        self.magic_config = FilePath(self.mktemp())
        self.global_config = create_testing_configuration(
            self.magic_config,
            FilePath(u"/no/tahoe/node-directory"),
        )

    @inline_callbacks
    def test_happy(self):
        """
        A file is successfully added.
        """
        stdout = StringIO()
        stderr = StringIO()

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                (b"post",
                 self.url.child("magic-folder", "default", "snapshot").to_text(),
                 {b"path": [b"foo"]},
                 {
                     b'Host': [b'invalid.'],
                     b'Content-Length': [b'0'],
                     b'Connection': [b'close'],
                     b'Authorization': [b'Bearer ' + self.global_config.api_token],
                     b'Accept-Encoding': [b'gzip']
                 },
                 b""),
                (200, {}, b"{}")
            ),
        ])
        http_client = StubTreq(
            StringStubbingResource(
                request_sequence,
            )
        )
        client = create_magic_folder_client(
            Clock(),
            self.global_config,
            http_client,
        )
        with request_sequence.consume(self.fail):
            yield dispatch_magic_folder_api_command(
                ["--config", self.magic_config.path, "add-snapshot",
                 "--file", "foo",
                 "--folder", "default"],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("{}")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Equals("")
        )

    @inline_callbacks
    def test_bad_file(self):
        """
        Adding a file outside the magic-folder fails
        """
        stdout = StringIO()
        stderr = StringIO()

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                (b"post",
                 self.url.child("magic-folder", "default", "snapshot").to_text(),
                 {b"path": [b"../../../foo"]},
                 {
                     b'Host': [b'invalid.'],
                     b'Content-Length': [b'0'],
                     b'Connection': [b'close'],
                     b'Authorization': [b'Bearer ' + self.global_config.api_token],
                     b'Accept-Encoding': [b'gzip']
                 },
                 b""),
                (406, {}, b'{"reason": "a really good one"}')
            ),
        ])
        http_client = StubTreq(
            StringStubbingResource(
                request_sequence,
            )
        )
        client = create_magic_folder_client(
            Clock(),
            self.global_config,
            http_client,
        )

        with self.assertRaises(SystemExit):
            with request_sequence.consume(self.fail):
                yield dispatch_magic_folder_api_command(
                    ["--config", self.magic_config.path, "add-snapshot",
                     "--file", "../../../foo",
                     "--folder", "default"],
                    stdout=stdout,
                    stderr=stderr,
                    client=client,
                )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Equals('{"reason": "a really good one"}')
        )


class TestMagicApi(AsyncTestCase):
    """
    Tests related to 'magic-folder-api' in general
    """
    url = DecodedURL.from_text(u"http://invalid./v1/")

    def test_load_config(self):
        """
        Correctly loads existing configuration
        """
        basedir = FilePath(self.mktemp())
        create_global_configuration(basedir, "tcp:-1", FilePath("/dev/null"), "tcp:127.0.0.1:-1")
        options = MagicFolderApiCommand()
        options.parseOptions([
            "--config", basedir.path,
            "add-snapshot", "--file", "foo", "--folder", "asdf",
        ])
        self.assertThat(
            options.config,
            IsInstance(GlobalConfigDatabase),
        )
        self.assertThat(
            options.client,
            IsInstance(MagicFolderClient),
        )
        self.assertThat(
            options.parent,
            Equals(None),
        )
        self.assertThat(
            str(options),
            StartsWith("Usage: magic-folder-api"),
        )

    def test_bad_config_fails(self):
        """
        Trying to load non-existant config fails
        """
        options = MagicFolderApiCommand()
        with self.assertRaises(UsageError):
            options.parseOptions([
                "--config", self.mktemp(),
                "add-snapshot", "--file", "foo", "--folder", "asdf",
            ])
            options.config  # accessing the config fails; it can't be loaded

    def test_no_subcommand(self):
        """
        Must specify a subcommand
        """
        options = MagicFolderApiCommand()
        with self.assertRaises(UsageError):
            options.parseOptions([
                "--config", self.mktemp(),
            ])

    @inline_callbacks
    def test_empty_command_prints_help(self):
        """
        User doesn't have to do --help
        """
        stdout = StringIO()
        with self.assertRaises(SystemExit):
            yield dispatch_magic_folder_api_command([], stdout=stdout)

        self.assertThat(
            stdout.getvalue(),
            Contains("Error: must specify a subcommand")
        )
        self.assertThat(
            stdout.getvalue(),
            Contains("Usage: magic-folder-api")
        )

    def test_version(self):
        """
        Version is displayed with --version
        """
        stdout = StringIO()
        stderr = StringIO()

        self.assertThat(
            dispatch_magic_folder_api_command(
                ["--version"],
                stdout=stdout,
                stderr=stderr,
                client=None,
            ),
            failed(
                AfterPreprocessing(
                    lambda f: isinstance(f.value, SystemExit) and f.value.code,
                    Equals(0)
                )
            )
        )
        from .. import __version__
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("magic-folder-api version {}".format(__version__))
        )

    def test_no_file_arg(self):
        """
        An error is printed if we don't specify --file
        """
        stdout = StringIO()
        stderr = StringIO()

        self.assertThat(
            dispatch_magic_folder_api_command(
                ["add-snapshot"],
                stdout=stdout,
                stderr=stderr,
                client=None,
            ),
            failed(
                AfterPreprocessing(
                    lambda f: isinstance(f.value, SystemExit) and f.value.code,
                    Equals(1)
                )
            )
        )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("Error: --file / -f is required")
        )

    def test_no_folder_arg(self):
        """
        An error is printed if we don't specify --folder
        """
        stdout = StringIO()
        stderr = StringIO()

        self.assertThat(
            dispatch_magic_folder_api_command(
                ["add-snapshot", "--file", "foo"],
                stdout=stdout,
                stderr=stderr,
                client=None,
            ),
            failed(
                AfterPreprocessing(
                    lambda f: isinstance(f.value, SystemExit) and f.value.code,
                    Equals(1)
                )
            )
        )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("Error: --folder / -n is required")
        )

    @inline_callbacks
    def test_no_access(self):
        """
        An error is reported if we can't access the API at all
        """
        stdout = StringIO()
        stderr = StringIO()

        basedir = FilePath(self.mktemp())
        global_config = create_global_configuration(
            basedir,
            "tcp:-1",
            FilePath(u"/no/tahoe/node-directory"),
            "tcp:127.0.0.1:-1",
        )
        http_client = StubTreq(None)
        client = create_magic_folder_client(
            Clock(),
            global_config,
            http_client,
        )

        # simulate a failure to connect

        def error(*args, **kw):
            raise CannotAccessAPIError(
                "Can't reach the magic folder daemon at all"
            )
        client.add_snapshot = error

        with self.assertRaises(SystemExit):
            yield dispatch_magic_folder_api_command(
                ["--config", basedir.path, "add-snapshot",
                 "--file", "foo",
                 "--folder", "default"],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Contains("Error: Can't reach the magic folder daemon")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Contains("tcp:127.0.0.1:-1")
        )

    @inline_callbacks
    def test_api_error(self):
        """
        An error is reported if the API reports an error
        """
        stdout = StringIO()
        stderr = StringIO()

        basedir = FilePath(self.mktemp())
        global_config = create_global_configuration(
            basedir,
            "tcp:-1",
            FilePath(u"/no/tahoe/node-directory"),
            "tcp:127.0.0.1:-1",
        )

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                (b"post",
                 self.url.child("magic-folder", "default", "snapshot").to_text(),
                 {b"path": [b"foo"]},
                 {
                     b'Host': [b'invalid.'],
                     b'Content-Length': [b'0'],
                     b'Connection': [b'close'],
                     b'Authorization': [b'Bearer ' + global_config.api_token],
                     b'Accept-Encoding': [b'gzip']
                 },
                 b""),
                (406, {}, b'{"reason": "an explanation"}')
            ),
        ])
        http_client = StubTreq(
            StringStubbingResource(
                request_sequence,
            )
        )
        client = create_magic_folder_client(
            Clock(),
            global_config,
            http_client,
        )

        with self.assertRaises(SystemExit):
            with request_sequence.consume(self.fail):
                yield dispatch_magic_folder_api_command(
                    ["--config", basedir.path, "add-snapshot",
                     "--file", "foo",
                     "--folder", "default"],
                    stdout=stdout,
                    stderr=stderr,
                    client=client,
                )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("")
        )
        self.assertThat(
            json.loads(stderr.getvalue()),
            Equals({"reason": "an explanation"})
        )

    @inline_callbacks
    def test_unknown_error(self):
        """
        'Unexpected' exceptions cause an error to be printed
        """
        stdout = StringIO()
        stderr = StringIO()

        basedir = FilePath(self.mktemp())
        global_config = create_global_configuration(
            basedir,
            "tcp:-1",
            FilePath(u"/no/tahoe/node-directory"),
            "tcp:127.0.0.1:-1",
        )
        http_client = StubTreq(None)
        client = create_magic_folder_client(
            Clock(),
            global_config,
            http_client,
        )

        # simulate some kind of "unexpected" error upon .add_snapshot
        the_bad_stuff = RuntimeError("Something has gone wrong")

        def error(*args, **kw):
            raise the_bad_stuff
        client.add_snapshot = error

        with self.assertRaises(SystemExit):
            yield dispatch_magic_folder_api_command(
                ["--config", basedir.path, "add-snapshot",
                 "--file", "foo",
                 "--folder", "default"],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Contains("Error: {}".format(the_bad_stuff))
        )


class TestDumpState(AsyncTestCase):
    """
    Tests related to 'magic-folder-api dump-state'
    """

    def setUp(self):
        super(TestDumpState, self).setUp()
        self.magic_config = FilePath(self.mktemp())
        self.global_config = create_testing_configuration(
            self.magic_config,
            FilePath(u"/no/tahoe/node-directory"),
        )

    @inline_callbacks
    def test_happy(self):
        """
        Test printing of some well-known state
        """

        author = create_local_author("zara")
        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        config = self.global_config.create_magic_folder(
            name="test",
            magic_path=magic_path,
            author=author,
            collective_dircap=Capability.from_string("URI:DIR2:hz46fi2e7gy6i3h4zveznrdr5q:i7yc4dp33y4jzvpe5jlaqyjxq7ee7qj2scouolumrfa6c7prgkvq"),
            upload_dircap=Capability.from_string("URI:DIR2:hnua3xva2meb46dqm3ndmiqxhe:h7l2qnydoztv7gruwd65xtdhsvd3cm2kk2544knp5fhmzxoyckba"),
            poll_interval=1,
            scan_interval=1,
        )
        config.store_local_snapshot(
            LocalSnapshot(
                "foo",
                author,
                {},
                config.magic_path.child("foo"),
                parents_local=[],
                parents_remote=[],
            ),
            get_pathinfo(config.magic_path.child("foo")).state,
        )
        config.store_downloaded_snapshot(
            "bar",
            RemoteSnapshot(
                "bar",
                author,
                {"modification_time": 0},
                capability=Capability.from_string("URI:DIR2-CHK:l7b3rn6pha6c2ipbbo4yxvunvy:c6ppejrkip4cdfo3kmyju36qbb6bbptzhh3pno7jb5b5myzoxkja:1:5:329"),
                parents_raw=[],
                content_cap=random_immutable(),
                metadata_cap=random_immutable(),
            ),
            PathState(
                0,
                0,
                0,
            ),
        )

        # we've set up some particular, well-known state in our
        # configuration; now we exercise the dump-state subcommand and
        # ensure the output matches
        options = MagicFolderApiCommand()
        options.stdout = StringIO()
        options.stderr = StringIO()
        options.parseOptions([
            "--config", self.magic_config.path,
            "dump-state",
            "--folder", "test",
        ])
        options._config = self.global_config
        yield run_magic_folder_api_options(options)

        self.assertThat(
            options.stderr.getvalue(),
            Equals("")
        )
        stdout_lines_no_whitespace = [
            line.strip()
            for line in options.stdout.getvalue().splitlines()
        ]

        local_uuid = config.get_local_snapshot("foo").identifier

        self.assertThat(
            stdout_lines_no_whitespace,
            Equals([
                config.name,
                "author: zara {}".format(author.signing_key.verify_key.encode(encoder=HexEncoder).decode("utf8")),
                "stash_path: {}".format(config.stash_path.path),
                "magic_path: {}".format(config.magic_path.path),
                "collective: URI:DIR2:hz46fi2e7gy6i3h4zveznrdr5q:i7yc4dp33y4jzvpe5jlaqyjxq7ee7qj2scouolumrfa6c7prgkvq",
                "local snapshots:",
                "foo: {}".format(local_uuid),
                "remote snapshots:",
                "bar:",
                "cap: URI:DIR2-CHK:l7b3rn6pha6c2ipbbo4yxvunvy:c6ppejrkip4cdfo3kmyju36qbb6bbptzhh3pno7jb5b5myzoxkja:1:5:329",
                "mtime: 0",
                "size: 0",
            ])
        )


class TestApiParticipants(AsyncTestCase):
    """
    Tests related to 'magic-folder-api add-participant' and
    'magic-folder-api list-participants'
    """
    url = DecodedURL.from_text(u"http://invalid./v1/")

    def setUp(self):
        super(TestApiParticipants, self).setUp()
        self.magic_config = FilePath(self.mktemp())
        self.global_config = create_testing_configuration(
            self.magic_config,
            FilePath(u"/no/tahoe/node-directory"),
        )

    @inline_callbacks
    def test_add_participant_missing_arg(self):
        """
        If arguments are missing, an error is reported
        """
        stdout = StringIO()
        stderr = StringIO()

        # missing --personal-dmd
        with self.assertRaises(SystemExit):
            yield dispatch_magic_folder_api_command(
                ["--config", self.magic_config.path, "add-participant",
                 "--folder", "default",
                 "--author-name", "amaya",
                ],
                stdout=stdout,
                stderr=stderr,
                client=None,
            )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("Error: --personal-dmd / -p is required")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Equals("")
        )

    @inline_callbacks
    def XXXtest_add_participant(self):
        """
        A new participant is added to a magic-folder
        """
        stdout = StringIO()
        stderr = StringIO()

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                # expected request
                (b"post",
                 self.url.child("magic-folder", "default", "participants").to_text(),
                 {},
                 {
                     b'Host': [b'invalid.'],
                     b'Content-Length': [b'149'],
                     b'Connection': [b'close'],
                     b'Authorization': [b'Bearer ' + self.global_config.api_token],
                     b'Accept-Encoding': [b'gzip']
                 },
                 # XXX args, this fails because of different sorting of keys in body serialization
                 json.dumps({
                     "personal_dmd": "URI:DIR2-CHK:lq34kr5sp7mnvkhce4ahl2nw4m:dpujdl7sol6xih5gzil525tormolzaucq4re7snn5belv7wzsdha:1:5:328",
                     "author": {
                         "name": "amaya",
                     }
                 }).encode("utf8"),
                ),
                # expected response
                (200, {}, b"{}"),
            ),
        ])
        http_client = StubTreq(
            StringStubbingResource(
                request_sequence,
            )
        )
        client = create_magic_folder_client(
            Clock(),
            self.global_config,
            http_client,
        )
        with request_sequence.consume(self.fail):
            yield dispatch_magic_folder_api_command(
                ["--config", self.magic_config.path, "add-participant",
                 "--folder", "default",
                 "--author-name", "amaya",
                 "--personal-dmd", 'URI:DIR2-CHK:lq34kr5sp7mnvkhce4ahl2nw4m:dpujdl7sol6xih5gzil525tormolzaucq4re7snn5belv7wzsdha:1:5:328',
                ],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("{}")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Equals("")
        )

    @inline_callbacks
    def test_list_participants_missing_arg(self):
        """
        An error is reported if argument missing
        """
        stdout = StringIO()
        stderr = StringIO()

        # --folder missing
        with self.assertRaises(SystemExit):
            yield dispatch_magic_folder_api_command(
                ["--config", self.magic_config.path, "list-participants",
                ],
                stdout=stdout,
                stderr=stderr,
                client=None,
            )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("Error: --folder / -n is required")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Equals("")
        )

    @inline_callbacks
    def test_list_participants(self):
        """
        List all participants in a magic-folder
        """
        stdout = StringIO()
        stderr = StringIO()

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                # expected request
                (b"get",
                 self.url.child("magic-folder", "default", "participants").to_text(),
                 {},
                 {
                     b'Host': [b'invalid.'],
                     b'Connection': [b'close'],
                     b'Authorization': [b'Bearer ' + self.global_config.api_token],
                     b'Accept-Encoding': [b'gzip']
                 },
                 b"",
                ),
                # expected response
                (200, {}, b"{}"),
            ),
        ])
        http_client = StubTreq(
            StringStubbingResource(
                request_sequence,
            )
        )
        client = create_magic_folder_client(
            Clock(),
            self.global_config,
            http_client,
        )
        with request_sequence.consume(self.fail):
            yield dispatch_magic_folder_api_command(
                ["--config", self.magic_config.path, "list-participants",
                 "--folder", "default",
                ],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )
        self.assertThat(
            stdout.getvalue().strip(),
            Equals("{}")
        )
        self.assertThat(
            stderr.getvalue().strip(),
            Equals("")
        )


class TestApiMonitor(AsyncTestCase):
    """
    Tests related to 'magic-folder-api monitor'
    """
    url = DecodedURL.from_text(u"http://invalid./v1/")

    def setUp(self):
        super(TestApiMonitor, self).setUp()
        self.magic_config = FilePath(self.mktemp())
        self.global_config = create_testing_configuration(
            self.magic_config,
            FilePath(u"/no/tahoe/node-directory"),
        )
        self.reactor = MemoryReactorClockResolver()
        self.pumper = create_pumper()
        self.service = WebSocketStatusService(
            self.reactor,
            self.global_config,
        )
        self.factory = StatusFactory(self.service)
        self.agent = create_memory_agent(
            self.reactor,
            self.pumper,
            lambda: self.factory.buildProtocol(None)
        )
        return self.pumper.start()

    def tearDown(self):
        super(TestApiMonitor, self).tearDown()
        return self.pumper.stop()

    @inline_callbacks
    def test_once(self):
        """
        Output a single status message with --once option
        """
        stdout = StringIO()
        stderr = StringIO()

        yield dispatch_magic_folder_api_command(
            ["--config", self.magic_config.path, "monitor",
             "--once",
            ],
            stdout=stdout,
            stderr=stderr,
            websocket_agent=self.agent,
            config=self.global_config,
        )
        self.pumper._flush()

        self.assertThat(
            json.loads(stdout.getvalue()),
            Equals({
                'state': {
                    'folders': {},
                    'synchronizing': False,
                }
            }),
        )
