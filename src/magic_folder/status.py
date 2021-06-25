from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import json
import attr
from collections import (
    defaultdict,
)

from zope.interface import (
    Interface,
    implementer,
)

from autobahn.twisted.websocket import (
    WebSocketServerFactory,
    WebSocketServerProtocol,
)

from twisted.application import (
    service,
)


class IStatus(Interface):
    """
    An internal API for services to report realtime status
    information. These don't necessarily correspond 1:1 to outgoing
    messages from the status API.
    """

    def upload_queued(folder, relpath):
        """
        An item is added to our upload queue

        :param unicode folder: the name of the folder that started upload
        :param unicode relpath: relative local path of the snapshot
        """

    def upload_started(folder, relpath):
        """
        Started sending a Snapshot to Tahoe

        :param unicode folder: the name of the folder that started upload
        :param unicode relpath: relative local path of the snapshot
        """

    def upload_finished(folder, relpath):
        """
        Sending of a Snapshot to Tahoe has completed

        :param unicode folder: the name of the folder that started upload
        :param unicode relpath: relative local path of the snapshot
        """

    def download_started(folder, relpath):
        """
        Started downloading a Snapshot + content from Tahoe

        :param unicode folder: the name of the folder that started download
        :param unicode relpath: relative local path of the snapshot
        """

    def download_finished(folder, relpath):
        """
        Completed downloading and synchronizing a Snapshot from Tahoe

        :param unicode folder: the name of the folder that started download
        :param unicode relpath: relative local path of the snapshot
        """


class StatusProtocol(WebSocketServerProtocol):
    """
    Speaks the server side of the WebSocket status protocol, usually
    mounted at /v1/status from our web API.

    This is authenticated with the same Bearer token as the rest of
    the /v1 API.
    """

    def __init__(self, status):
        self.status = status
        WebSocketServerProtocol.__init__(self)

    def onOpen(self):
        """
        WebSocket API: successful handshake
        """
        self.status.client_connected(self)

    def onClose(self, wasClean, code, reason):
        """
        WebSocket API: we've lost our connection for some reason
        """
        self.status.client_disconnected(self)

    def onMessage(self, payload, isBinary):
        """
        WebSocket API: a message has been received from the client. This
        should never happen in our protocol.
        """
        self.sendClose(
            code=4000,
            reason="Unexpected incoming message",
        )


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

    def buildProtocol(self, addr):
        """
        IFactory API
        """
        protocol = self.protocol(self._status)
        protocol.factory = self
        return protocol


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

    # reactor
    _clock = attr.ib()

    # global configuration
    _config = attr.ib()

    # tracks currently-connected clients
    _clients = attr.ib(default=attr.Factory(set))

    # the last state we marshaled. This is the last state we sent out
    # and any newly connecting client will receive it immediately.
    _last_state = attr.ib(default=None)

    # current live state
    _folders = attr.ib(default=attr.Factory(lambda: defaultdict(lambda: defaultdict(dict))))

    def client_connected(self, protocol):
        """
        Called via the WebSocket protocol when a client has successfully
        completed the handshake (and authentication).

        Push the current state to the client immediately.
        """
        self._clients.add(protocol)
        if self._last_state is None:
            self._last_state = self._marshal_state()
        protocol.sendMessage(self._last_state)

    def client_disconnected(self, protocol):
        """
        Called via the WebSocket protocol when a client disconnects (for
        whatever reason). If this is the last client, we'll start
        buffering any messages.
        """
        self._clients.remove(protocol)

    def _marshal_state(self):
        """
        Internal helper. Turn our current notion of the state into a
        utf8-encoded byte-string of the JSON representing our current
        state.
        """
        upload_activity = any(
            len(folder["uploads"])
            for folder in self._folders.values()
        )
        download_activity = any(
            len(folder["downloads"])
            for folder in self._folders.values()
        )

        def folder_data_for(name):
            most_recent = [
                {relpath: {"updated": timestamp}}
                for relpath, timestamp in self._config.get_magic_folder(name).get_recent_remotesnapshot_paths(30)
            ]
            return {
                "uploads": self._folders.get(name, {}).get("uploads", {}),
                "downloads": self._folders.get(name, {}).get("downloads", {}),
                "recent": most_recent,
            }

        return json.dumps({
            "state": {
                "synchronizing": upload_activity or download_activity,
                "folders": {
                    name: folder_data_for(name)
                    for name in self._config.list_magic_folders()
                }
            }
        }).encode("utf8")

    def _maybe_update_clients(self):
        """
        Internal helper.

        Re-marshal our current state and compare it to the last sent
        state. If it is different, update all connected clients.
        """
        proposed_state = self._marshal_state()
        if self._last_state != proposed_state:
            self._last_state = proposed_state
            for client in self._clients:
                try:
                    client.sendMessage(self._last_state)
                except Exception as e:
                    print("Failed to send status: {}".format(e))
                    # XXX disconnect / remove client?

    # IStatus API

    def upload_queued(self, folder, relpath):
        """
        IStatus API
        """
        data = {
            "name": relpath,
            "queued_at": self._clock.seconds(),
        }
        self._folders[folder]["uploads"][relpath] = data
        self._maybe_update_clients()

    def upload_started(self, folder, relpath):
        """
        IStatus API
        """
        self._folders[folder]["uploads"][relpath]["started_at"] = self._clock.seconds()
        self._maybe_update_clients()

    def upload_finished(self, folder, relpath):
        """
        IStatus API
        """
        del self._folders[folder]["uploads"][relpath]
        self._maybe_update_clients()

    def download_started(self, folder, relpath):
        """
        IStatus API
        """
        data = {
            "name": relpath,
            "started_at": self._clock.seconds(),
        }
        self._folders[folder]["downloads"][relpath] = data
        self._maybe_update_clients()

    def download_finished(self, folder, relpath):
        """
        IStatus API
        """
        del self._folders[folder]["downloads"][relpath]
        self._maybe_update_clients()
