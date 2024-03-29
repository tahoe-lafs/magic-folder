import json

from testtools.matchers import (
    Equals,
    MatchesDict,
    MatchesListwise,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    Clock,
)
from eliot.twisted import (
    inline_callbacks,
)
from autobahn.twisted.testing import (
    create_memory_agent,
    MemoryReactorClockResolver,
    create_pumper,
)
from autobahn.twisted.websocket import (
    WebSocketClientProtocol,
)

from .common import (
    SyncTestCase,
    AsyncTestCase,
)
from ..status import (
    StatusFactory,
    EventsWebSocketStatusService,
)
from ..invite import (
    Invite,
)
from ..config import (
    create_testing_configuration,
)


class StatusServiceTests(SyncTestCase):
    """
    Tests relating to the status service
    """

    def setUp(self):
        super(StatusServiceTests, self).setUp()
        self.clock = Clock()
        self.basedir = FilePath(self.mktemp())
        self.tahoe_node_dir = FilePath(self.mktemp())
        self.tahoe_node_dir.makedirs()

        self.global_config = create_testing_configuration(
            self.basedir,
            self.tahoe_node_dir,
        )
        self.service = EventsWebSocketStatusService(
            self.clock,
            self.global_config,
        )

    def test_single_client(self):
        """
        With a single connected client, that client receives updates
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        self.service.client_connected(ClientProtocol())
        self.service.upload_queued("foo", "foo")
        self.clock.advance(1)
        self.service.upload_started("foo", "foo")
        self.clock.advance(1)
        self.service.upload_finished("foo", "foo")

        self.assertThat(
            messages,
            Equals([
                {
                    "events": [{
                        "kind": "tahoe-connection-changed",
                        "connected": 0,
                        "desired": 0,
                        "happy": False,
                    }]
                },
                {
                    "events": [{
                        "kind": "upload-queued",
                        "folder": "foo",
                        "timestamp": 0.0,
                        "relpath": "foo",
                    }]
                },
                {
                    "events": [{
                        "kind": "upload-started",
                        "folder": "foo",
                        "timestamp": 1.0,
                        "relpath": "foo",
                    }]
                },
                {
                    "events": [{
                        "kind": "upload-finished",
                        "folder": "foo",
                        "timestamp": 2.0,
                        "relpath": "foo",
                    }]
                },
            ])
        )

    def test_offline_client(self):
        """
        A client gets the correct state when connecting
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        self.service.upload_queued("foo", "foo")
        self.service.upload_started("foo", "foo")
        self.service.download_queued("foo", "bar")
        self.service.download_started("foo", "bar")
        self.assertThat(messages, Equals([]))

        # once connected, this client should get the proper state
        self.service.client_connected(ClientProtocol())

        self.assertThat(
            messages,
            Equals([{
                "events": [
                    {"folder": "foo", "kind": "folder-added"},
                    {
                        "folder": "foo",
                        "kind": "upload-queued",
                        "timestamp": 0.0,
                        "relpath": "foo",
                    },
                    {
                        "folder": "foo",
                        "kind": "upload-started",
                        "relpath": "foo",
                        "timestamp": 0.0,
                    },
                    {
                        "folder": "foo",
                        "kind": "download-queued",
                        "timestamp": 0.0,
                        "relpath": "bar",
                    },
                    {
                        "folder": "foo",
                        "kind": "download-started",
                        "relpath": "bar",
                        "timestamp": 0.0,
                    },
                    {
                        "connected": 0,
                        "desired": 0,
                        "happy": False,
                        "kind": "tahoe-connection-changed"
                    }
                ]
            }])
        )


    def test_offline_client_invites(self):
        """
        A client gets the correct state when connecting, with invite
        events
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        inv = Invite(
            "fake-uuid", "invitee name", "read-only",
            collection=object(),
            wormhole=object(),
            status=self.service,
        )
        self.service.invite_created("foo", inv)
        self.service.invite_welcomed("foo", inv, {"motd": "hello, world"})
        self.service.invite_code_created("foo", inv, "1-foo-bar")
        self.service.invite_versions("foo", inv, {"magic-wormhole": {}})
        self.assertThat(messages, Equals([]))

        # once connected, this client should get the proper state
        self.service.client_connected(ClientProtocol())

        self.assertThat(
            messages,
            MatchesListwise([
                MatchesDict({
                    "events": MatchesListwise([
                        Equals({"folder": "foo", "kind": "folder-added"}),
                        Equals({"folder": "foo", "kind": "invite-created", "mode": "read-only", "participant-name": "invitee name", "id": "fake-uuid"}),
                        Equals({"folder": "foo", "kind": "invite-welcomed", "mode": "read-only", "participant-name": "invitee name", "id": "fake-uuid", "welcome": {"motd": "hello, world"}}),
                        Equals({"folder": "foo", "kind": "invite-code-created", "mode": "read-only", "participant-name": "invitee name", "id": "fake-uuid", "code": "1-foo-bar"}),
                        Equals({"folder": "foo", "kind": "invite-versions-received", "mode": "read-only", "participant-name": "invitee name", "id": "fake-uuid", "versions": {"magic-wormhole": {}}}),
                        Equals({"connected": 0, "desired": 0, "happy": False, "kind": "tahoe-connection-changed"}),
                    ])
                })
            ])
        )

    def test_disconnect(self):
        """
        A client disconnecting and re-connecting gets correct state
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        client = ClientProtocol()
        self.service.client_connected(client)
        self.service.client_disconnected(client)
        self.assertThat(
            messages,
            Equals([{
                "events": [
                    {"connected": 0, "desired": 0, "happy": False, "kind": "tahoe-connection-changed"},
                ]
            }])
        )

        # change our state
        self.service.upload_queued("foo", "foo")

        # re-connect the client; it should get the (latest) state as
        # well as the initial state it got on the first connect
        self.service.client_connected(client)

        self.assertThat(
            messages,
            Equals([
                {
                    "events": [
                        {"connected": 0, "desired": 0, "happy": False, "kind": "tahoe-connection-changed"},
                    ]
                },
                {
                    "events": [
                        {"folder": "foo", "kind": "folder-added"},
                        {"folder": "foo", "kind": "upload-queued", "timestamp": 0.0, "relpath": "foo"},
                        {"connected": 0, "desired": 0, "happy": False, "kind": "tahoe-connection-changed"},
                    ]
                }
            ])
        )

    def test_client_error(self):
        """
        We log an error if a message to a client fails to send
        """

        class ClientProtocol(object):
            def __init__(self, do_error):
                self.do_error = do_error
                self.messages = []

            def sendMessage(self, payload):
                self.messages.append(json.loads(payload))
                if len(self.messages) == 2 and self.do_error:
                    raise RuntimeError("loopback is broken?")

        client0 = ClientProtocol(do_error=True)
        client1 = ClientProtocol(do_error=False)
        self.service.client_connected(client0)
        self.service.client_connected(client1)

        # change our state
        self.service.upload_queued("foo", "foo")

        # client1 has a send-error .. so it is removed from the list
        # of clients, and then the error (i.e. "failing to send") is
        # reported to all other connected clients.
        self.assertThat(
            client0.messages,
            Equals([
                {
                    "events": [
                        {"connected": 0, "desired": 0, "happy": False, "kind": "tahoe-connection-changed"}
                    ]
                },
                {
                    "events": [
                        {"folder": "foo", "kind": "upload-queued", "timestamp": 0.0, "relpath": "foo"}
                    ]
                },
            ])
        )


class WebSocketTests(AsyncTestCase):
    """
    Tests relating to the actual WebSocket protocol of the status
    serice
    """

    def setUp(self):
        super(WebSocketTests, self).setUp()
        self.reactor = MemoryReactorClockResolver()
        self.pumper = create_pumper()
        self.tahoe_node_dir = FilePath(self.mktemp())
        self.tahoe_node_dir.makedirs()
        self.global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            self.tahoe_node_dir,
        )

        self.service = EventsWebSocketStatusService(
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

    @inline_callbacks
    def tearDown(self):
        yield super(WebSocketTests, self).tearDown()
        yield self.pumper.stop()

    @inline_callbacks
    def test_open(self):
        """
        When the WebSocket connects it receives a state update
        """

        messages = []

        class TestProto(WebSocketClientProtocol):
            def onMessage(self, msg, is_binary):
                messages.append(json.loads(msg))

        # upon open, we should receive the current state
        proto = yield self.agent.open("ws://127.0.0.1:2/v1/status", {}, TestProto)
        self.pumper._flush()
        self.assertThat(
            messages,
            Equals([
                {
                    "events": [
                        {"connected": 0, "desired": 0, "happy": False, "kind": "tahoe-connection-changed"},
                    ]
                }
            ])
        )

        # if we change the state, we should receive an update
        self.service.upload_queued("foo", "foo")
        self.pumper._flush()
        self.assertThat(
            messages,
            Equals([
                {
                    "events": [
                        {"connected": 0, "desired": 0, "happy": False, "kind": "tahoe-connection-changed"},
                    ]
                },
                {
                    "events": [
                        {"folder": "foo", "kind": "upload-queued", "timestamp": 0.0, "relpath": "foo"}
                    ]
                },
            ])
        )
        proto.dropConnection()
        yield proto.is_closed

    def test_send_message(self):
        """
        Sending a message is a protocol error
        """

        closed = []

        class TestProto(WebSocketClientProtocol):
            def onOpen(self):
                self.sendMessage(b"bogus")

            def onClose(self, was_clean, code, reason):
                closed.append(reason)

        # we send a message, which is a protocol violation .. so we
        # should see a disconnect
        self.agent.open("ws://127.0.0.1:6/v1/status", {}, TestProto)
        self.pumper._flush()
        self.assertThat(
            closed,
            Equals([
                "Unexpected incoming message",
            ])
        )
