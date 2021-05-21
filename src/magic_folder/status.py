
import json
import attr

from zope.interface import (
    Interface,
    Attribute,
    implementer,
)

from autobahn.twisted.websocket import (
    WebSocketServerFactory,
    WebSocketServerProtocol,
)

from twisted.internet.defer import (
    inlineCallbacks,
)
from twisted.application import service


class StatusProtocol(WebSocketServerProtocol):
    """
    Speaks the server side of the WebSocket status protocol, usually
    mounted at /v1/status from our web API.

    This is authenticated with the same Bearer token as the rest of
    the /v1 API.
    """

    def onOpen(self):
        """
        WebSocket API: successful handshake
        """
        self.factory._status.client_connected(self)

    def onClose(self, wasClean, code, reason):
        """
        WebSocket API: we've lost our connection for some reason
        """
        self.factory._status.client_disconnected(self)

    def onMessage(self, payload, isBinary):
        """
        WebSocket API: a message has been received from the client. This
        should never happen in our protocol.
        """
        pass


class StatusFactory(WebSocketServerFactory):
    """
    Instantiates server-side StatusProtocol instances when clients
    connect.
    """
    protocol = StatusProtocol

    def __init__(self, status):
        """
        :param WebSocketStatusService status: actual provider of our
            status information. The protocol will use this to track
            clients as they connect and disconnect.
        """
        self._status = status
        WebSocketServerFactory.__init__(self, server="magic-folder")


class IStatus(Interface):
    """
    An internal API for services to report realtime status
    information. These don't necessarily correspond 1:1 to outgoing
    messages from the status API.
    """

    def upload_started():
        """
        One or more items are now in our upload queue.
        """

    def upload_stopped():
        """
        No items are in the upload queue.
        """


@attr.s
@implementer(service.IService)
@implementer(IStatus)
class WebSocketStatusService(service.Service):
    """
    A global service that can be used to report status information via
    an authenticated WebSocket connection.

    The authentication mechanism is the same as for the HTTP API (see
    web.py where a WebSocketResource is mounted into the resource
    tree).
    """

    # tracks currently-connected clients
    _clients = attr.ib(default=attr.Factory(set))

    # if zero clients are connected we keep all messages until some
    # client connects
    _pending_messages = attr.ib(default=attr.Factory(list))

    # in order to only do edge-triggered messages we track whether
    # there is upload (and later downloading) going on right now.
    _uploading = attr.ib(default=False)

    def client_connected(self, protocol):
        """
        Called via the WebSocket protocol when a client has successfully
        completed the handshake (and authentication).

        If we have any pending messages, those are all pushed to this client.
        """
        self._clients.add(protocol)
        while self._pending_messages:
            msg = self._pending_messages.pop(0)
            protocol.sendMessage(msg)

    def client_disconnected(self, protocol):
        """
        Called via the WebSocket protocol when a client disconnects (for
        whatever reason). If this is the last client, we'll start
        buffering any messages.
        """
        self._clients.remove(protocol)

    def _send_message(self, msg):
        """
        Internal helper. Send a status message (or queue it for later if
        we have no clients right now).

        :param dict msg: a dict containing only JSON-able contents
        """
        payload = json.dumps(msg).encode("utf8")
        if not self._clients:
            self._pending_messages.append(payload)
        else:
            for client in self._clients:
                try:
                    client.sendMessage(payload)
                except Exception as e:
                    # XXX should log this somewhere
                    pass

    # IStatus API

    def upload_started(self):
        """
        IStatus API
        """
        if not self._uploading:
            self._uploading = True
            self._send_message({
                "kind": "synchronizing",
                "status": True,
            })

    def upload_stopped(self):
        """
        IStatus API
        """
        if self._uploading:
            self._uploading = False
            self._send_message({
                "kind": "synchronizing",
                "status": False,
            })


@implementer(IStatus)
class NullStatusService(service.Service):
    """
    An IStatus service that doesn't do anything
    """

    def upload_started(self):
        pass

    def upload_stopped(self):
        pass
