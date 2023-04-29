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

    def to_json(self):
        return {
            "happy": self.is_happy,
            "connected": self.connected,
            "desired": self.desired,
        }


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
            "timestamp": ns_to_seconds_float(self.last_completed) if self.last_completed else None,
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
            "timestamp": ns_to_seconds_float(self.last_completed) if self.last_completed else None,
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

        :param unicode message: a message suitable for an end-user to
            read that describes the error. Such a message MUST NOT
            include any secrects such as Tahoe capabilities.
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

    def folder_added(folder):
        """
        A new folder has been created.

        :param unicode folder: the name of the folder
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


def _marshal_event(kind, event_data):
    event_data["kind"] = kind
    return event_data


def _marshal_event_error(folder_name, err):
    msg = err.to_json()
    msg["folder"] = folder_name
    return _marshal_event("error-occurred", msg)


def _marshal_event_scan_completed(folder_name, scanner):
    msg = scanner.to_json()
    msg["folder"] = folder_name
    return _marshal_event("scan-completed", msg)


def _marshal_event_poll_completed(folder_name, poller):
    msg = poller.to_json()
    msg["folder"] = folder_name
    return _marshal_event("poll-completed", msg)


def _marshal_event_tahoe(tahoe):
    return _marshal_event("tahoe-connection-changed", tahoe.to_json())


def _marshal_event_upload_queued(folder_name, relpath, queued_at):
    return {
        "kind": "upload-queued",
        "folder": folder_name,
        "relpath": relpath,
        "timestamp": queued_at,
    }


def _marshal_event_upload_started(folder_name, relpath, started_at):
    return {
        "kind": "upload-started",
        "folder": folder_name,
        "relpath": relpath,
        "timestamp": started_at,
    }


def _marshal_event_upload_finished(folder_name, relpath, finished_at):
    return {
        "kind": "upload-finished",
        "folder": folder_name,
        "relpath": relpath,
        "timestamp": finished_at,
    }


def _marshal_event_download_queued(folder_name, relpath, queued_at):
    return _marshal_event(
        "download-queued", {
            "folder": folder_name,
            "relpath": relpath,
            "timestamp": queued_at,
        }
    )


def _marshal_event_download_started(folder_name, relpath, started_at):
    return _marshal_event(
        "download-started", {
            "folder": folder_name,
            "relpath": relpath,
            "timestamp": started_at,
        }
    )


def _marshal_event_download_finished(folder_name, relpath, finished_at):
    return {
        "kind": "download-finished",
        "folder": folder_name,
        "relpath": relpath,
        "timestamp": finished_at,
    }


def _marshal_event_folder_added(folder_name):
    return {
        "kind": "folder-added",
        "folder": folder_name,
    }


def _marshal_event_folder_left(folder_name):
    return {
        "kind": "folder-left",
        "folder": folder_name,
    }


@attr.s
@implementer(service.IService)
@implementer(IStatus)
class EventsWebSocketStatusService(service.Service):
    """
    A global service that can be used to report status information via
    an authenticated WebSocket connection.

    The authentication mechanism is the same as for the HTTP API (see
    web.py where a WebSocketResource is mounted into the resource
    tree).

    All messages look like this::

        {"events": []}

    ...where the list contains events, which are event-specific
    serialization of the the event and will always contain the
    ``"kind"`` key::

        {
            "kind": "the sort of event",
        }
    """

    # reactor
    _clock = attr.ib()

    # global configuration
    _config = attr.ib()

    # maximum number of recent errors to retain (new clients will see
    # at most this number of errors)
    max_errors = attr.ib(default=30)

    # tracks currently-connected clients
    _clients = attr.ib(default=attr.Factory(set))

    # current live state
    _folders = attr.ib(default=attr.Factory(lambda: defaultdict(_create_blank_folder_state)))
    _tahoe = attr.ib(default=attr.Factory(lambda: TahoeStatus(0, 0, False)))

    def stopService(self):
        """
        Disconnect all clients upon shutdown
        """
        for client in self._clients:
            client.dropConnection()

    def client_connected(self, protocol):
        """
        Called via the WebSocket protocol when a client has successfully
        completed the handshake (and authentication).

        Push the current state to the client immediately.
        """
        self._clients.add(protocol)
        protocol.sendMessage(self._marshal_state())

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

        This bundles up a bunch of 'event' messages that represent the
        current overall state.

        Note that we _don't_ replay every single message, so the
        transcript of a session that saw a bunch of changes live will
        be different from a transcript of a new session on the same
        state. They should arrive at the same point, however. A simple
        example:

        - folder "foo" added
        - folder "bar" added
        - folder "foo" deleted

        A new client will see a bundle of messages with just and "add"
        for folder "bar" -- whereas a client connected to entire time
        will see the above events.
        """
        events = []
        for foldername, folder in self._folders.items():
            events.append(_marshal_event_folder_added(foldername))
            # XXX reverse-sort via "queued-at"?
            for relpath, up in folder["uploads"].items():
                events.append(
                    _marshal_event_upload_queued(foldername, relpath, queued_at=up["queued-at"])
                )
                if "started-at" in up:
                    events.append(
                        _marshal_event_upload_started(foldername, relpath, started_at=up["started-at"])
                    )
                # if this had finished, it would already be deleted from the list
            for relpath, down in folder["downloads"].items():
                events.append(
                    _marshal_event_download_queued(foldername, relpath, queued_at=down["queued-at"])
                )
                if "started-at" in down:
                    events.append(
                        _marshal_event_download_started(foldername, relpath, started_at=down["started-at"])
                    )
                # if this had finished, it would already be deleted from the list

            for err in folder.get("errors", []):
                events.append(_marshal_event_error(foldername, err))

            if folder["scanner"].last_completed is not None:
                events.append(_marshal_event_scan_completed(foldername, folder["scanner"]))
            if folder["poller"].last_completed is not None:
                events.append(_marshal_event_poll_completed(foldername, folder["poller"]))

        events.append(_marshal_event_tahoe(self._tahoe))

        return json.dumps({
            "events": events,
        }).encode("utf8")

    def _send_single_event(self, msg_json):
        """
        Internal helper.

        Send the given message dict as a single event to all connected clients.
        """
        for client in self._clients:
            try:
                client.sendMessage(json.dumps({"events": [msg_json]}).encode("utf8"))
            except Exception as e:
                self.error_occurred(None, "Failed to send status: {}".format(e))
                # XXX disconnect / remove client?

    # IStatus API

    def folder_added(self, folder):
        """
        IStatus API

        :param unicode folder: the folder which has been added
        """
        self._send_single_event(_marshal_event_folder_added(folder))
        # a blank entry in the state will be created on-demand

    def folder_gone(self, folder):
        """
        IStatus API

        :param unicode folder: the folder which is removed
        """
        self._send_single_event(_marshal_event_folder_left(folder))
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
            self._send_single_event(_marshal_event_tahoe(self._tahoe))

    def scan_status(self, folder, status):
        """
        IStatus API
        """
        self._folders[folder]["scanner"] = status
        self._send_single_event(_marshal_event_scan_completed(folder, status))

    def poll_status(self, folder, status):
        """
        IStatus API
        """
        self._folders[folder]["poller"] = status
        self._send_single_event(_marshal_event_poll_completed(folder, status))

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
        self._send_single_event(_marshal_event_error(folder, err))

    def upload_queued(self, folder, relpath):
        """
        IStatus API
        """
        # it's permitted to call this API more than once on the same
        # relpath, but we should keep the _oldest_ queued time.
        if relpath not in self._folders[folder]["uploads"]:
            queued_at = self._clock.seconds()
            self._folders[folder]["uploads"][relpath] = {
                "relpath": relpath,
                "queued-at": queued_at,
            }
            self._send_single_event(_marshal_event_upload_queued(folder, relpath, queued_at))

    def upload_started(self, folder, relpath):
        """
        IStatus API
        """
        started_at = self._clock.seconds()
        self._folders[folder]["uploads"][relpath]["started-at"] = started_at
        self._send_single_event(_marshal_event_upload_started(folder, relpath, started_at))

    def upload_finished(self, folder, relpath):
        """
        IStatus API
        """
        del self._folders[folder]["uploads"][relpath]
        self._send_single_event(_marshal_event_upload_finished(folder, relpath, self._clock.seconds()))

    def download_queued(self, folder, relpath):
        """
        IStatus API
        """
        # it's permitted to call this API more than once on the same
        # relpath, but we should keep the _oldest_ queued time.
        if relpath not in self._folders[folder]["downloads"]:
            queued_at = self._clock.seconds()
            self._folders[folder]["downloads"][relpath] = {
                "relpath": relpath,
                "queued-at": queued_at,
            }
            self._send_single_event(_marshal_event_download_queued(folder, relpath, queued_at))

    def download_started(self, folder, relpath):
        """
        IStatus API
        """
        started_at = self._clock.seconds()
        self.download_queued(folder, relpath)  # ensure relpath exists
        self._folders[folder]["downloads"][relpath]["started-at"] = started_at
        self._send_single_event(_marshal_event_download_started(folder, relpath, started_at))

    def download_finished(self, folder, relpath):
        """
        IStatus API
        """
        del self._folders[folder]["downloads"][relpath]
        self._send_single_event(_marshal_event_download_finished(folder, relpath, self._clock.seconds()))


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
