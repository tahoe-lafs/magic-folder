from __future__ import unicode_literals

import json
from six.moves import (
    StringIO,
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
from twisted.internet.defer import (
    inlineCallbacks,
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

from ..client import (
    create_magic_folder_client,
    MagicFolderClient,
    CannotAccessAPIError,
)
from ..api_cli import (
    dispatch_magic_folder_api_command,
    MagicFolderApiCommand,
)
from ..config import (
    create_testing_configuration,
    create_global_configuration,
    GlobalConfigDatabase,
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

    def tearDown(self):
        super(TestApiAddSnapshot, self).tearDown()

    @inlineCallbacks
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
                 self.url.child("snapshot").child("default").to_text().encode("utf8"),
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

    @inlineCallbacks
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
                 self.url.child("snapshot").child("default").to_text().encode("utf8"),
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
            options.get_client(),
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

    @inlineCallbacks
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

    @inlineCallbacks
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

    @inlineCallbacks
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
                 self.url.child("snapshot").child("default").to_text().encode("utf8"),
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

    @inlineCallbacks
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
