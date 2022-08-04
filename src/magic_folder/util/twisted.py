# Copyright (c) Least Authority TFA GmbH.

"""
Utilities for interacting with twisted.
"""


from functools import wraps

import attr
from eliot import write_failure
from eliot.twisted import (
    inline_callbacks,
)
from twisted.application.service import Service
from twisted.internet.defer import (
    Deferred,
    maybeDeferred,
    CancelledError,
)
from twisted.internet.interfaces import (
    IDelayedCall,
    IReactorTime,
)
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


def cancelled(*args, **kw):
    """
    A function that takes any arguments at all and returns a Deferred
    that is already cancelled.
    """
    d = Deferred()
    d.cancel()
    return d


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
    # if "interval" is 0 we only schedule calls explicitly, not periodically
    _interval = attr.ib(validator=attr.validators.instance_of((int, type(None))))
    _callable = attr.ib(validator=attr.validators.is_callable())

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
    _should_call = attr.ib(init=False, default=False)

    def startService(self):
        super(PeriodicService, self).startService()
        if self._interval is not None:
            self.call_soon()

    @inline_callbacks
    def stopService(self):
        yield super(PeriodicService, self).stopService()
        self._cancel_delayed_call()
        if self._deferred is not None:
            yield self._deferred

    def call_soon(self):
        """
        Call the function soon.

        This may be immediately, if we are waiting between calls. Or it may be
        after the current call to the function is complete.

        :returns Deferred[None]: fires when the scheduled call completes.
        """
        self._should_call = True
        self._schedule()
        d = Deferred()

        def completed(arg):
            d.callback(None)
            return arg

        def error(f):
            d.errback(f)
            return None

        if self._deferred:
            self._deferred.addCallbacks(completed, error)
        else:
            d.callback(None)
        return d

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
            if isinstance(result, Failure):
                if isinstance(result.value, CancelledError):
                    if self._deferred is not None:
                        self._deferred.cancel()
                write_failure(result)
            self._deferred = None
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

        - If the function is running, we don't do anything;
          this method will be called again when the function is finished.
        - If a call has been requested, start it now.
        - Otherwise, if we haven't scheduled a future call, we scheduled it
          the given interval into the future.
        """
        if self._deferred is not None:
            return
        if self._should_call:
            self._should_call = False
            self._call()
        elif self._delayed_call is None:
            if self._interval is not None:
                self._delayed_call = self._clock.callLater(self._interval, self._call)
