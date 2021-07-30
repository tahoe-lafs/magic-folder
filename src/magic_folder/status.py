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

from .util.file import (
    seconds_to_ns,
    ns_to_seconds,
)


class IStatus(Interface):
    """
    An internal API for services to report realtime status
    information. These don't necessarily correspond 1:1 to outgoing
    messages from the status API.
    """

    def error_occurred(public_error):
        """
        Some error happened that should be reported to the user.

        :param PublicError public_error: a plain-language description
            of the error.
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


def _create_blank_folder_state():
    """
    Internal helper. Create the blank state for a new folder in the
    status-service state.
    """
    return {
        "uploads": {},
        "downloads": {},
        "recent": [],
        "errors": [],
    }


@attr.s
class PublicError(object):
    """
    Description of an error that is permissable to show to a UI.

    Such an error MUST NOT reveal any secret information, which
    includes at least: any capability-string or any fURL.

    The langauge used in the error should be plain and simple,
    avoiding jargon and technical details (except where immediately
    relevant). This is used by the IStatus API.
    """
    timestamp = attr.ib(validator=attr.validators.instance_of((float, int, long)))
    summary = attr.ib(validator=attr.validators.instance_of(unicode))

    def to_json(self):
        """
        :returns: a dict suitable for serializing to JSON
        """
        return {
            "timestamp": ns_to_seconds(self.timestamp),
            "summary": self.summary,
        }

    def __str__(self):
        return self.summary


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

    # maximum number of recent errors to retain
    max_errors = attr.ib(default=30)

    # tracks currently-connected clients
    _clients = attr.ib(default=attr.Factory(set))

    # the last state we marshaled. This is the last state we sent out
    # and any newly connecting client will receive it immediately.
    _last_state = attr.ib(default=None)

    # current live state
    _folders = attr.ib(default=attr.Factory(lambda: defaultdict(_create_blank_folder_state)))

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
                {
                    "relpath": relpath,
                    "modified": timestamp,
                    "last-updated": last_updated,
                }
                for relpath, timestamp, last_updated
                in self._config.get_magic_folder(name).get_recent_remotesnapshot_paths(30)
            ]
            uploads = [
                upload
                for upload in sorted(
                        self._folders.get(name, {}).get("uploads", {}).values(),
                        key=lambda u: u.get("queued-at", 0),
                        reverse=True,
                )
            ]
            downloads = [
                download
                for download in sorted(
                        self._folders.get(name, {}).get("downloads", {}).values(),
                        key=lambda d: d.get("queued-at", 0),
                        reverse=True,
                )
            ]
            return {
                "uploads": uploads,
                "downloads": downloads,
                "errors": [
                    err.to_json()
                    for err in self._folders.get(name, {}).get("errors", [])
                ],
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

    def error_occurred(self, folder, message):
        """
        IStatus API

        :param unicode folder: the folder this error pertains to

        :param unicode message: a message suitable for an end-user to
            read that describes the error. Such a message MUST NOT
            include any secrects such as Tahoe capabilities.
        """
        err = PublicError(
            seconds_to_ns(self._clock.seconds()),
            message,
        )
        self._folders[folder]["errors"].insert(0, err)
        self._folders[folder]["errors"] = self._folders[folder]["errors"][:self.max_errors]
        self._maybe_update_clients()

    def upload_queued(self, folder, relpath):
        """
        IStatus API
        """
        # it's permitted to call this API more than once on the same
        # relpath, but we should keep the _oldest_ queued time.
        if relpath not in self._folders[folder]["uploads"]:
            self._folders[folder]["uploads"][relpath] = {
                "name": relpath,
                "queued-at": self._clock.seconds(),
            }
        self._maybe_update_clients()

    def upload_started(self, folder, relpath):
        """
        IStatus API
        """
        self._folders[folder]["uploads"][relpath]["started-at"] = self._clock.seconds()
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
            "started-at": self._clock.seconds(),
        }
        self._folders[folder]["downloads"][relpath] = data
        self._maybe_update_clients()

    def download_finished(self, folder, relpath):
        """
        IStatus API
        """
        del self._folders[folder]["downloads"][relpath]
        self._maybe_update_clients()
