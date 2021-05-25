from __future__ import (
    unicode_literals,
    print_function,
)

import json

from testtools.matchers import (
    Equals,
)

from .common import (
    SyncTestCase,
)
from ..status import (
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
        With a single connected client, that client receives an update
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        self.service.client_connected(ClientProtocol())
        self.service.upload_started()

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

    def test_offline_client(self):
        """
        The first client to connect gets the correct state
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        self.service.upload_started()
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
        After a connect + disconnect messages are buffered
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
        self.service.upload_started()

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
