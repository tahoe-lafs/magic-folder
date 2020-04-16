from twisted.internet.defer import (
    Deferred,
    succeed,
)


class OneShotObserver(object):
    """
    Multiple users can observe an event which happens at most once.

    This is a helper for classes to implement the 'when_*()'
    asynchronous idiom that gives a fresh Deferred to every caller and
    notifies those Deferreds when an event happens. This looks like:

        def __init__(self):
            self.door_opened = OneShotObserver()

        def when_opened(self):
            return self.door_opened.when_fired()

        def the_door_is_open(self):
            # ...
            self.door_opened.fire(True)

    So user-code would await on .when_opened() and receive True when
    the door opens. If there is some error, .fire() may be called with
    a Failure which will deliver an exception to the awaiters.
    """

    def __init__(self):
        self._fired = False
        self._result = None
        self._watchers = []

    def when_fired(self):
        """
        :returns: a new Deferred that will fire when this has a result
            (so it may already be fired if we already have a result).
        """
        if self._fired:
            return succeed(self._result)
        d = Deferred()
        self._watchers.append(d)
        return d

    def fire(self, result):
        """
        Notify all observers and mark this event as fired with the given
        result (which may be a Failure if this is an error).
        """
        assert not self._fired
        self._fired = True
        self._result = result
        for w in self._watchers:
            w.callback(result)
        self._watchers = []
