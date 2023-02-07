import json
from io import (
    StringIO,
)

from twisted.internet.interfaces import (
    IStreamServerEndpoint,
)
from twisted.internet.testing import (
    MemoryReactorClock,
)
from twisted.internet.task import (
    Clock,
)
from twisted.internet.defer import (
    succeed,
    inlineCallbacks,
)
from twisted.python.failure import (
    Failure,
)
from twisted.python.usage import (
    UsageError,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.runtime import (
    platform,
)
from zope.interface import (
    implementer,
)
from hyperlink import (
    DecodedURL,
)

import attr

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    Equals,
    ContainsDict,
    Contains,
)
from treq.testing import (
    RequestSequence,
    StringStubbingResource,
    StubTreq,
)
from .common import (
    AsyncTestCase,
    SyncTestCase,
)
from .fixtures import (
    NodeDirectory,
)
from ..config import (
    load_global_configuration,
    create_testing_configuration,
    describe_experimental_features,
)
from ..endpoints import (
    CannotConvertEndpointError,
)
from ..cli import (
    BaseOptions,
    on_stdin_close,
    dispatch_magic_folder_command,
    maybe_fail_experimental_command,
)
from ..client import (
    create_magic_folder_client,
)
from magic_folder.util.observer import (
    ListenObserver,
)
from magic_folder.initialize import (
    magic_folder_initialize,
)
from magic_folder.migrate import (
    magic_folder_migrate,
)
from magic_folder.show_config import (
    magic_folder_show_config,
)


@attr.s
@implementer(IStreamServerEndpoint)
class EndpointForTesting(object):
    _responses = attr.ib(default=attr.Factory(list))

    def listen(self, factory):
        if not self._responses:
            return Failure(Exception("no more responses"))
        r = self._responses[0]
        self._responses = self._responses[1:]
        return succeed(r)


class TestListenObserver(AsyncTestCase):
    """
    Confirm operation of magic_folder.util.observer.ListenObserver
    """

    def test_good(self):
        ep = EndpointForTesting(["we listened"])
        obs = ListenObserver(endpoint=ep)
        d0 = obs.observe()
        d1 = obs.observe()

        self.assertFalse(d0.called)
        self.assertFalse(d1.called)


        result = obs.listen("not actually a factory")
        self.assertTrue(result.called)
        self.assertEqual(result.result, "we listened")

        d2 = obs.observe()

        for d in [d0, d1, d2]:
            self.assertTrue(d.called)
            self.assertEqual(d.result, "we listened")


class TestBaseOptions(SyncTestCase):
    """
    Confirm operations of BaseOptions features
    """

    def setUp(self):
        super(TestBaseOptions, self).setUp()
        self.base = FilePath(self.mktemp())
        self.base.makedirs()
        self.options = BaseOptions()
        self.options['config'] = self.base.path

    def test_client_endpoint(self):
        with self.base.child("api_client_endpoint").open("w") as f:
            f.write(b"not running\n")
        with self.assertRaises(Exception):
            self.options.api_client_endpoint

    def test_invalid_api_token(self):
        """
        if the api_token file somehow becomes invalid and error is raised
        """
        with self.base.child("api_token").open("w") as f:
            f.write(b"not base64")
        with self.assertRaises(Exception) as ctx:
            self.options.api_token
        self.assertThat(
            str(ctx.exception),
            Contains("Invalid base64")
        )

    def test_short_api_token(self):
        """
        if the api_token file somehow becomes invalid and error is raised
        """
        with self.base.child("api_token").open("w") as f:
            f.write(b'Zm9v')  # valid base64, but too short
        with self.assertRaises(Exception) as ctx:
            self.options.api_token
        self.assertThat(
            str(ctx.exception),
            Contains("Incorrect token data")
        )

class TestExperimental(SyncTestCase):
    """
    Tests relating to experimental commands
    """

    def setUp(self):
        super(TestExperimental, self).setUp()
        self.basedir = self.mktemp()
        self.tahoedir = self.mktemp()
        self.config = create_testing_configuration(FilePath(self.basedir), FilePath(self.tahoedir))
        self.options = BaseOptions()
        self.options._config = self.config

    def test_fail_unenabled(self):
        """
        trying to run un-enabled experimental commands fails
        """
        self.options.subCommand = "invite"
        with self.assertRaises(UsageError):
            maybe_fail_experimental_command(self.options)


class TestInitialize(SyncTestCase):
    """
    Confirm operation of 'magic-folder initialize' command
    """

    def setUp(self):
        super(TestInitialize, self).setUp()
        self.temp = FilePath(self.mktemp())
        self.node_dir = self.useFixture(NodeDirectory(self.temp.child("node")))

    def test_good(self):
        magic_folder_initialize(
            self.temp.child("good"),
            u"tcp:1234",
            self.node_dir.path,
            u"tcp:localhost:1234",
            u"ws://localhost.invalid/",  # dummy magic-wormhole URL
        )


class TestMigrate(SyncTestCase):
    """
    Confirm operation of 'magic-folder migrate' command
    """

    def setUp(self):
        super(TestMigrate, self).setUp()
        self.temp = FilePath(self.mktemp())
        self.magic_path = self.temp.child("magic")
        self.magic_path.makedirs()
        self.node_dir = self.useFixture(NodeDirectory(self.temp.child("node")))
        self.node_dir.create_magic_folder(
            u"test-folder",
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            self.magic_path,
            60,
        )

    def test_good(self):
        magic_folder_migrate(
            self.temp.child("new_magic"),
            u"tcp:1234",
            self.node_dir.path,
            u"alice",
            u"tcp:localhost:1234",
        )
        config = load_global_configuration(self.temp.child("new_magic"))
        self.assertThat(
            list(config.list_magic_folders()),
            Equals([u"test-folder"]),
        )

    def test_bad_listen_string(self):
        """
        Passing a completely invalid 'endpoint listen string' (not even a
        string) is an error
        """
        with ExpectedException(CannotConvertEndpointError):
            magic_folder_migrate(
                self.temp.child("new_magic"),
                "1234",
                self.node_dir.path,
                u"alice",
                None,
            )

    def test_invalid_listen_string(self):
        """
        Passing a non-string (bytes) for the listen-endpoint is an error
        """
        with ExpectedException(ValueError):
            magic_folder_migrate(
                self.temp.child("new_magic"),
                b"1234",  # only accepts unicode
                self.node_dir.path,
                u"alice",
                b"invalid",
            )

    def test_bad_connect_string(self):
        """
        Passing an un-parsable connect-endpoint-string is an error
        """
        with ExpectedException(ValueError):
            magic_folder_migrate(
                self.temp.child("new_magic"),
                u"tcp:1234",
                self.node_dir.path,
                u"alice",
                "localhost:1234",
            )


class TestShowConfig(SyncTestCase):
    """
    Confirm operation of 'magic-folder show-config' command
    """

    def setUp(self):
        super(TestShowConfig, self).setUp()
        self.temp = FilePath(self.mktemp())
        self.magic_path = self.temp.child("magic")
        self.magic_path.makedirs()
        self.node_dir = self.useFixture(NodeDirectory(self.temp.child("node")))
        self.node_dir.create_magic_folder(
            u"test-folder",
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            self.magic_path,
            60,
        )

    def test_good(self):
        magic_folder_initialize(
            self.temp.child("good"),
            u"tcp:1234",
            self.node_dir.path,
            u"tcp:localhost:1234",
            u"ws://localhost.invalid/",  # dummy magic-wormhole URL
        )
        stdout = StringIO()
        config = load_global_configuration(self.temp.child("good"))
        magic_folder_show_config(
            config,
            stdout=stdout,
        )
        self.assertThat(
            json.loads(stdout.getvalue()),
            ContainsDict({
                u'api_endpoint': Equals(u'tcp:1234'),
                u'tahoe_node_directory': Equals(self.node_dir.path.path),
                u'magic_folders': Equals({}),
            })
        )


class TestSetConfig(AsyncTestCase):
    """
    Confirm operation of 'magic-folder set-config' command
    """
    url = DecodedURL.from_text(u"http://invalid./v1/")

    def setUp(self):
        super(TestSetConfig, self).setUp()
        self.magic_config = FilePath(self.mktemp())
        self.global_config = create_testing_configuration(
            self.magic_config,
            FilePath(u"/no/tahoe/node-directory"),
        )

    @inlineCallbacks
    def test_enable_feature(self):
        """
        enable an optional feature
        """
        stdout = StringIO()
        stderr = StringIO()

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                (b"post",
                 self.url.child("config", "enable-feature", "invites").to_text(),
                 {},
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
            yield dispatch_magic_folder_command(
                ["--config", self.magic_config.path, "set-config",
                 "--enable", "invites",
                ],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )

    @inlineCallbacks
    def test_disable_feature_already_disabled(self):
        """
        try to disable an already disabled feature
        """
        stdout = StringIO()
        stderr = StringIO()

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                (b"post",
                 self.url.child("config", "disable-feature", "invites").to_text(),
                 {},
                 {
                     b'Host': [b'invalid.'],
                     b'Content-Length': [b'0'],
                     b'Connection': [b'close'],
                     b'Authorization': [b'Bearer ' + self.global_config.api_token],
                     b'Accept-Encoding': [b'gzip']
                 },
                 b""),
                (400, {}, b'{"reason": "some kind of error"}')
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
            yield dispatch_magic_folder_command(
                ["--config", self.magic_config.path, "set-config",
                 "--disable", "invites",
                ],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )
        self.assertThat(
            stderr.getvalue(),
            Contains("some kind of error")
        )

    @inlineCallbacks
    def test_disable_feature(self):
        """
        disable an optional feature
        """
        stdout = StringIO()
        stderr = StringIO()

        # 2-tuples of "expected request" and the corresponding reply
        request_sequence = RequestSequence([
            # ((method, url, params, headers, data), (code, headers, body)),
            (
                (b"post",
                 self.url.child("config", "disable-feature", "invites").to_text(),
                 {},
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
            yield dispatch_magic_folder_command(
                ["--config", self.magic_config.path, "set-config",
                 "--disable", "invites",
                ],
                stdout=stdout,
                stderr=stderr,
                client=client,
            )

    @inlineCallbacks
    def test_list_features(self):
        """
        list optional features
        """
        stdout = StringIO()
        stderr = StringIO()

        yield dispatch_magic_folder_command(
            ["--config", self.magic_config.path, "set-config",
             "--features",
            ],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertThat(
            stdout.getvalue(),
            Contains(describe_experimental_features())
        )


class TestStdinClose(SyncTestCase):
    """
    Confirm operation of on_stdin_close
    """

    def test_close_called(self):
        """
        our on-close method is called when stdin closes
        """
        reactor = MemoryReactorClock()
        called = []

        def onclose():
            called.append(True)
        proto = on_stdin_close(reactor, onclose)
        self.assertThat(called, Equals([]))

        if platform.isWindows():
            # it seems we can't close stdin/stdout (from "inside"?) on
            # Windows, so cheat. (See also comment/implementation in
            # _pollingfile.py in Twisted)
            proto.writeConnectionLost()
            proto.readConnectionLost()
        else:
            for reader in reactor.getReaders():
                reader.loseConnection()
            reactor.advance(1)  # ProcessReader does a callLater(0, ..)

        self.assertThat(
            called,
            Equals([True])
        )

    def test_exception_ignored(self):
        """
        an exception from or on-close function is ignored
        """
        reactor = MemoryReactorClock()
        called = []

        def onclose():
            called.append(True)
            raise RuntimeError("unexpected error")
        proto = on_stdin_close(reactor, onclose)
        self.assertThat(called, Equals([]))

        if platform.isWindows():
            # it seems we can't close stdin/stdout (from "inside"?) on
            # Windows, so cheat. (See also comment/implementation in
            # _pollingfile.py in Twisted)
            proto.writeConnectionLost()
            proto.readConnectionLost()
        else:
            for reader in reactor.getReaders():
                reader.loseConnection()
            reactor.advance(1)  # ProcessReader does a callLater(0, ..)

        self.assertThat(
            called,
            Equals([True])
        )
