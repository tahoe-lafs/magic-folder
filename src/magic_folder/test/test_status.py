from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import json

from testtools.matchers import (
    Equals,
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


class StatusServiceTests(SyncTestCase):
    """
    Tests relating to the status service
    """

    def setUp(self):
        super(StatusServiceTests, self).setUp()
        self.service = WebSocketStatusService()

    def test_single_client(self):
        """
        With a single connected client, that client receives updates
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        self.service.client_connected(ClientProtocol())
        self.service.upload_started("foo")
        self.service.upload_stopped("foo")

        self.assertThat(
            messages,
            Equals([{
                "state": {
                    "synchronizing": False,
                }
            }, {
                "state": {
                    "synchronizing": True,
                }
            }, {
                "state": {
                    "synchronizing": False,
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

        self.service.upload_started("foo")
        self.assertThat(messages, Equals([]))

        # once connected, this client should get the proper state
        self.service.client_connected(ClientProtocol())

        self.assertThat(
            messages,
            Equals([{
                "state": {
                    "synchronizing": True,
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
                }
            }])
        )

        # change our state
        self.service.upload_started("foo")

        # re-connect the client; it should get the (latest) state as
        # well as the initial state it got on the first connect
        self.service.client_connected(client)
        self.assertThat(
            messages,
            Equals([{
                "state": {
                    "synchronizing": False,
                }
            }, {
                "state": {
                    "synchronizing": True,
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
        self.service = WebSocketStatusService()
        self.factory = StatusFactory(self.service)
        self.agent = create_memory_agent(
            self.reactor,
            self.pumper,
            lambda: self.factory.buildProtocol(None)
        )
        return self.pumper.start()

    def tearDown(self):
        super(WebSocketTests, self).tearDown()
        return self.pumper.stop()

    def test_open(self):
        """
        When the WebSocket connects it receives a state update
        """

        messages = []

        class TestProto(WebSocketClientProtocol):
            def onMessage(self, msg, is_binary):
                messages.append(json.loads(msg))

        # upon open, we should receive the current state
        self.agent.open("ws://127.0.0.1:-1/v1/status", {}, TestProto)
        self.pumper._flush()
        self.assertThat(
            messages,
            Equals([
                {
                    "state": {
                        "synchronizing": False,
                    }
                }
            ])
        )

        # if we change the state, we should receive an update
        self.service.upload_started("foo")
        self.pumper._flush()
        self.assertThat(
            messages,
            Equals([
                {
                    "state": {
                        "synchronizing": False,
                    }
                },
                {
                    "state": {
                        "synchronizing": True,
                    }
                }
            ])
        )

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
        self.agent.open("ws://127.0.0.1:-1/v1/status", {}, TestProto)
        self.pumper._flush()
        self.assertThat(
            closed,
            Equals([
                "Unexpected incoming message",
            ])
        )
