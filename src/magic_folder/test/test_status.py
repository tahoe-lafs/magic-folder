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
