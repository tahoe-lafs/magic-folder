
from twisted.internet.defer import inlineCallbacks
from twisted.internet.interfaces import IStreamServerEndpoint
from twisted.application.service import (
    IService,
    Service,
)

from zope.interface import implementer

import attr

from magic_folder.util.observer import OneShotObserver


@implementer(IService)
@attr.s
class StreamingServerService(Service):
    """
    An IService that listens on an endpoint at startup and provides
    feedback when the service is listening (or an error).
    """

    endpoint = attr.ib()
    factory = attr.ib()
    _waiting_for_port = None

    def __attrs_post_init__(self):
        Service.__init__(self)
        self._listening = OneShotObserver()

    def when_listening(self):
        """
        :returns: a Deferred that fires with `None` when this service is
            listening, or errbacks if listening fails.
        """
        return self._listening.when_fired()

    def privilegedStartService(self):
        """
        Start listening on the endpoint.
        """
        Service.privilegedStartService(self)
        self._waiting_for_port = self.endpoint.listen(self.factory)
        self._waiting_for_port.addBoth(self._listening.fire)

    def startService(self):
        """
        Start listening on the endpoint, unless L{privilegedStartService} got
        around to it already.
        """
        Service.startService(self)
        if self._waiting_for_port is None:
            self.privilegedStartService()

    @inlineCallbacks
    def stopService(self):
        """
        Stop listening on the port if it is already listening, otherwise,
        cancel the attempt to listen.

        :returns: a Deferred which fires with `None` when the port
            has stopped listening.
        """
        self._waiting_for_port.cancel()
        try:
            port = yield self._waiting_for_port
            if port:
                port.stopListening()
        finally:
            self.running = False

