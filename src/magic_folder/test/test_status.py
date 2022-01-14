import json

from testtools.matchers import (
    Equals,
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
    WebSocketStatusService,
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
        self.service = WebSocketStatusService(
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
        self.service.upload_started("foo", "foo")
        self.service.upload_finished("foo", "foo")

        self.assertThat(
            messages,
            Equals([{
                "state": {
                    "synchronizing": False,
                    "folders": {},
                }
            }, {
                "state": {
                    "synchronizing": True,
                    "folders": {},
                }
            }, {
                "state": {
                    "synchronizing": False,
                    "folders": {},
                }
            }])
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
        self.assertThat(messages, Equals([]))

        # once connected, this client should get the proper state
        self.service.client_connected(ClientProtocol())

        self.assertThat(
            messages,
            Equals([{
                "state": {
                    "synchronizing": True,
                    "folders": {},
                }
            }])
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
                "state": {
                    "synchronizing": False,
                    "folders": {},
                }
            }])
        )

        # change our state
        self.service.upload_queued("foo", "foo")

        # re-connect the client; it should get the (latest) state as
        # well as the initial state it got on the first connect
        self.service.client_connected(client)
        self.assertThat(
            messages,
            Equals([{
                "state": {
                    "synchronizing": False,
                    "folders": {},
                }
            }, {
                "state": {
                    "synchronizing": True,
                    "folders": {},
                }
            }])
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
                    "state": {
                        "synchronizing": False,
                        "folders": {},
                    }
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
                    "state": {
                        "synchronizing": False,
                        "folders": {},
                    }
                },
                {
                    "state": {
                        "synchronizing": True,
                        "folders": {},
                    }
                }
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
