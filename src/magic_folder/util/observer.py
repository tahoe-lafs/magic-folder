from twisted.internet.interfaces import (
    IStreamServerEndpoint,
)
from twisted.internet.defer import (
    succeed,
    Deferred,
)
from twisted.python.failure import (
    Failure,
)

import attr

from zope.interface import implementer


@attr.s
@implementer(IStreamServerEndpoint)
class ListenObserver(object):
    """
    Calls .listen on the given endpoint and allows observers to be
    notified when that listen succeeds (or fails).
    """
    _endpoint = attr.ib(validator=[attr.validators.provides(IStreamServerEndpoint)])
    _observers = attr.ib(default=attr.Factory(list))
    _listened_result = attr.ib(default=None)

    def observe(self):
        if self._listened_result is not None:
            return succeed(self._listened_result)
        self._observers.append(Deferred())
        return self._observers[-1]

    def listen(self, protocolFactory):
        d = self._endpoint.listen(protocolFactory)
        d.addBoth(self._deliver_result)
        return d

    def _deliver_result(self, result):
        self._listened_result = result
        observers = self._observers
        self._observers = []
        for o in observers:
            o.callback(result)
        if isinstance(result, Failure):
            # we've handled the error -- by passing it off to our
            # observer(s) -- so this chain doesn't need to anymore
            return None
        return result
