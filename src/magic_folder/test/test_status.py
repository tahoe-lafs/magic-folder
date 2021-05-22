from __future__ import print_function

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
                "kind": "synchronizing",
                "status": True,
            }])
        )

    def test_offline_client(self):
        """
        The first client to connect gets message backlog
        """
        messages = []

        class ClientProtocol(object):
            def sendMessage(self, payload):
                messages.append(json.loads(payload))

        self.service.upload_started()
        self.service.upload_stopped()
        self.assertThat(messages, Equals([]))

        # once connected, this client should get the message backlog
        # (in the right order)
        self.service.client_connected(ClientProtocol())

        self.assertThat(
            messages,
            Equals([{
                "kind": "synchronizing",
                "status": True,
            },{
                "kind": "synchronizing",
                "status": False,
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

        # now we should buffer a message
        self.service.upload_started()
        self.assertThat(messages, Equals([]))

        # re-connect the client; it should get the message
        self.service.client_connected(client)
        self.assertThat(
            messages,
            Equals([{
                "kind": "synchronizing",
                "status": True,
            }])
        )
