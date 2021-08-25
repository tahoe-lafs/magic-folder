# Copyright (c) Least Authority TFA GmbH.

"""
Utilities for interacting with twisted.
"""


from __future__ import absolute_import, division, print_function, unicode_literals

from functools import wraps

import attr
from eliot import write_failure
from twisted.application.service import Service
from twisted.internet.defer import Deferred, maybeDeferred, succeed
from twisted.internet.interfaces import IDelayedCall, IReactorTime
from twisted.python.failure import Failure


def exclusively(maybe_f=None, lock_name="_lock"):
    """
    A method decorator that aquires a py:`DeferredLock` while running the
    function.

    :param str lock_name: The name of the lock attribute to use.
    """

    def wrap(f):
        @wraps(f)
        def wrapper(self, *args, **kwargs):
            return getattr(self, lock_name).run(f, self, *args, **kwargs)

        return wrapper

    if maybe_f is None:
        return wrap
    else:
        return wrap(maybe_f)


@attr.s
class PeriodicService(Service):
    """
    Service to periodically and on-demand call a given function.

    When one or more requests to call the function arrive while the
    function is running, the service will cause the function to be called
    *once* immediately after the first call completes (though requests made
    during the second call will cause an additional call). The assumption is
    that new work for the function has arrived, but the function has likely
    already determined what work it will do in the current call.

    When a request to call the function arrives between periodic calls to
    the function, the timer is reset.

    Differences from :py:`twisted.application.internet.TimerService`:
    - The interval represents time between when one call ends and the next one
      starts, instead of between the interval between start times. This can
      lead to the function being called at irregular intervals, if it takes
      some time to run.

      The motivation is that the periodic runs is to retry operations that have
      failed. For example, if the function does enough work to overflow the
      interval and then fails due to a loss of network connectivity, there is
      no reason to try again immediately, instead of waiting.
    - The service takes a clock explicitly, rather than having an attribute to
      set to override the default reactor.
    - The service logs and ignores exceptions from function, rather than stopping
      periodic calls to the function.

    :ivar IReactorTime _clock: Source of time
    :ivar Callable[[], Deferred[None]] _callable:
        Function to call. It will not be called again until the deferred it
        retruns has fired.
    :ivar int interval: The time between periodic calls to the function.
    """
    _clock = attr.ib(validator=attr.validators.provides(IReactorTime))
    _callable = attr.ib(validator=attr.validators.is_callable())
    _interval = attr.ib(validator=attr.validators.instance_of(int))

    _delayed_call = attr.ib(
        init=False,
        default=None,
        validator=attr.validators.optional(attr.validators.provides(IDelayedCall)),
    )
    _deferred = attr.ib(
        init=False,
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(Deferred)),
    )
    _should_call = attr.ib(init=False, default=True)

    def startService(self):
        super(PeriodicService, self).startService()
        self._schedule()

    def stopService(self):
        super(PeriodicService, self).stopService()
        self._cancel_delayed_call()
        if self._deferred is not None:
            d = Deferred()
            self._deferred.chainDeferred(d)
            return d
        else:
            return succeed(None)

    def call_soon(self):
        """
        Call the function soon.

        This may be immediately, if we are waiting between calls. Or it may be
        after the current call to the function is complete.
        """
        self._should_call = True
        self._schedule()

    def _cancel_delayed_call(self):
        """
        Cancel any pending delayed call, and remove our record of it.
        """
        if self._delayed_call is not None:
            if self._delayed_call.active():
                self._delayed_call.cancel()
            self._delayed_call = None

    def _call(self):
        """
        Call the given function, and arrange to schedule the
        next call when it completes.
        """
        def cb(result):
            self._deferred = None
            if isinstance(result, Failure):
                write_failure(result)
            if self.running:
                self._schedule()

        # We've either been triggered by our delayed call (in which case we
        # clear our record of it), or we've been explicitly requested to call
        # the function (in which case we cancel it)
        self._cancel_delayed_call()
        self._deferred = maybeDeferred(self._callable)
        self._deferred.addBoth(cb)

    def _schedule(self):
        """
        Schedule a call to the given function.

        - If the service isn't running, we don't do anything;
          this method will be called again when the service is started.
        - If the function is running, we don't do anything;
          this method will be called again when the function is finished.
        - If a call has been requested, start it now.
        - Otherwise, if we haven't scheduled a future call, we scheduled it
          the given interval into the future.
        """
        if not self.running:
            return
        if self._deferred is not None:
            return
        if self._should_call:
            self._should_call = False
            self._call()
        elif self._delayed_call is None:
            self._delayed_call = self._clock.callLater(self._interval, self._call)
