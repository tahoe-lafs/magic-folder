import json
from functools import partial

import attr
from collections import (
    defaultdict,
)

from zope.interface import (
    Interface,
    implementer,
)
from zope.interface.interface import Method

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
    ns_to_seconds_float,
)


@attr.s(frozen=True)
class TahoeStatus:
    # number of connected servers (0 if we can't contact our client at all)
    connected = attr.ib(validator=attr.validators.instance_of(int))

    # number of servers we _want_ to connect to
    desired = attr.ib(validator=attr.validators.instance_of(int))

    # False if we can't get the welcome page at all
    is_connected = attr.ib(validator=attr.validators.instance_of(bool))

    @property
    def is_happy(self):
        return self.is_connected and (self.connected >= self.desired)


@attr.s(frozen=True)
class ScannerStatus:
    """
    Represents the current status of the scanner
    """
    # epoch, in nanoseconds, of the last completed run's end or None
    # if one isn't complete
    last_completed = attr.ib()

    def to_json(self):
        """
        :returns: a dict suitable for serializing to JSON
        """
        return {
            "last-scan": ns_to_seconds_float(self.last_completed) if self.last_completed else None,
        }


@attr.s(frozen=True)
class PollerStatus:
    """
    Represents the current status of the poller
    """
    # epoch, in nanoseconds, of the last completed run's end or None
    # if one isn't complete yet
    last_completed = attr.ib()

    def to_json(self):
        """
        :returns: a dict suitable for serializing to JSON
        """
        return {
            "last-poll": ns_to_seconds_float(self.last_completed) if self.last_completed else None,
        }


class IStatus(Interface):
    """
    An internal API for services to report realtime status
    information. These don't necessarily correspond 1:1 to outgoing
    messages from the status API.
    """

    def tahoe_status(status):
        """
        Update the status of our Tahoe-LAFS connection.
        :param TahoeStatus status: the current status
        """

    def scan_status(folder, status):
        """
        :param str folder: folder name
        :param ScannerStatus: the status
        """

    def poll_status(folder, status):
        """
        :param str folder: folder name
        :param PollerStatus: the status
        """

    def error_occurred(folder, message):
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

    def download_queued(folder, relpath):
        """
        An item is added to our download queue

        :param unicode folder: the name of the folder that started download
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

    def folder_gone(folder):
        """
        The given folder is gone and all state for it should be deleted.

        :param unicode folder: the folder that is gone
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
        "scanner": ScannerStatus(None),
        "poller": PollerStatus(None),
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
    timestamp = attr.ib(validator=attr.validators.instance_of((float, int)))
    summary = attr.ib(validator=attr.validators.instance_of(str))

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
    _tahoe = attr.ib(default=attr.Factory(lambda: TahoeStatus(0, 0, False)))

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
            mf_config = self._config.get_magic_folder(name)
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

            def uploads_and_downloads():
                for d in downloads:
                    yield d["relpath"]
                for d in uploads:
                    yield d["relpath"]
            down_up = set(uploads_and_downloads())

            most_recent = [
                {
                    "relpath": relpath,
                    "modified": timestamp,
                    "last-updated": last_updated,
                    "conflicted": bool(len(mf_config.list_conflicts_for(relpath))),
                }
                for relpath, timestamp, last_updated
                in self._config.get_magic_folder(name).get_recent_remotesnapshot_paths(30)
                if relpath not in down_up
            ]
            return {
                "uploads": uploads,
                "downloads": downloads,
                "errors": [
                    err.to_json()
                    for err in self._folders.get(name, {}).get("errors", [])
                ],
                "recent": most_recent,
                "tahoe": {
                    "happy": self._tahoe.is_happy,
                    "connected": self._tahoe.connected,
                    "desired": self._tahoe.desired,
                },
                "scanner": self._folders.get(name, {"scanner": ScannerStatus(None)})["scanner"].to_json(),
                "poller": self._folders.get(name, {"poller": PollerStatus(None)})["poller"].to_json(),
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

    def folder_gone(self, folder):
        """
        IStatus API

        :param unicode folder: the folder which is removed
        """
        try:
            del self._folders[folder]
        except KeyError:
            pass

    def tahoe_status(self, status):
        """
        IStatus API
        """
        if status != self._tahoe:
            self._tahoe = status
            self._maybe_update_clients()

    def scan_status(self, folder, status):
        """
        IStatus API
        """
        self._folders[folder]["scanner"] = status
        self._maybe_update_clients()

    def poll_status(self, folder, status):
        """
        IStatus API
        """
        self._folders[folder]["poller"] = status
        self._maybe_update_clients()

    def error_occurred(self, folder, message):
        """
        IStatus API

        :param str folder: the folder this error pertains to (or
            None for "all folders")

        :param str message: a message suitable for an end-user to
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
                "relpath": relpath,
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

    def download_queued(self, folder, relpath):
        """
        IStatus API
        """
        # it's permitted to call this API more than once on the same
        # relpath, but we should keep the _oldest_ queued time.
        if relpath not in self._folders[folder]["downloads"]:
            self._folders[folder]["downloads"][relpath] = {
                "relpath": relpath,
                "queued-at": self._clock.seconds(),
            }
        self._maybe_update_clients()

    def download_started(self, folder, relpath):
        """
        IStatus API
        """
        self.download_queued(folder, relpath)  # ensure relpath exists
        self._folders[folder]["downloads"][relpath]["started-at"] = self._clock.seconds()
        self._maybe_update_clients()

    def download_finished(self, folder, relpath):
        """
        IStatus API
        """
        del self._folders[folder]["downloads"][relpath]
        self._maybe_update_clients()


@attr.s
class _ProxyDescriptor(object):
    """
    Descriptor that returns ``partial(self.<original>.<method>, self.<relative>)``.
    """
    original = attr.ib(validator=attr.validators.instance_of(str))
    relative = attr.ib(validator=attr.validators.instance_of(str))
    method = attr.ib(validator=attr.validators.instance_of(str))

    def __get__(self, oself, type=None):
        if oself is None:
            raise NotImplementedError()
        original = getattr(oself, self.original)
        return partial(
            getattr(original, self.method),
            getattr(oself, self.relative),
        )

def relative_proxy_for(iface, original, relative):
    """
    Class decorator that partially applies methods of an interface.

    For each method of the given interface, that takes as first argument the
    given relative argument, this adds a corresponding proxy method to the
    decorated class, that gets that argument from the decorated instance.

    :param Interface iface: The interface to generate proxy methods for.
    :param unicode original: The attribute on the decorated class that
        contains the implementation of the interface.
    :param unicode relative: The attribute on the decorated class to
        pass as the first argument to methods of the interface, when
        the name of the first argument matches.
    """
    def decorator(cls):
        for name, method in iface.namesAndDescriptions():
            if not isinstance(method, Method):
                continue
            if method.positional[0] == relative:
                setattr(cls, name, _ProxyDescriptor(original, relative, name))
        return cls
    return decorator


@implementer(IStatus)
@relative_proxy_for(IStatus, "_status", "folder")
@attr.s
class FolderStatus(object):
    """
    Wrapper around an :py:`IStatus` implementation that automatically passes
    the ``folder`` argument.
    """
    folder = attr.ib(validator=attr.validators.instance_of(str))
    _status = attr.ib(validator=attr.validators.provides(IStatus))
