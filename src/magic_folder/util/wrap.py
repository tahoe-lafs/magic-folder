

class _Wrap(object):
    """
    A wrapper class that directs calls to a child object, except for
    explicit overrides.
    """

    def __init__(self, original, **kwargs):
        self._original = original
        self._wrapper_enabled = kwargs.pop("_wrapper_enabled", True)
        self._overrides = kwargs

    def enable_wrapper(self):
        self._wrapper_enabled = True

    def __getattr__(self, name):
        over = self._overrides.get(name, None)
        if not self._wrapper_enabled:
            over = None
        if over is not None:
            return over
        return getattr(self._original, name)


class _DelayedWrap(object):
    """
    A wrapper class that directs calls to a child object, except for
    explicit overrides. Until .enable_wrapper() is called, it does
    _not_ override any calls.
    """

    def __init__(self, original, **kwargs):
        self._original = original
        self._wrapper_enabled = False
        self._overrides = kwargs

    def enable_wrapper(self):
        self._wrapper_enabled = True

    def __getattr__(self, name):
        over = self._overrides.get(name, None)
        if not self._wrapper_enabled:
            over = None
        if over is not None:
            return over
        return getattr(self._original, name)


def wrap_frozen(original, **kwargs):
    """
    :param object original: the original immutable object to wrap

    :param dict kwargs: mapping names to values, all these are
        overridden in the returned wrapper.

    :returns: an object that behaves like the original except for the
        overridden attributes found in kwargs.
    """
    return _Wrap(original, **kwargs)


def delayed_wrap_frozen(original, **kwargs):
    """
    :param object original: the original immutable object to wrap

    :param dict kwargs: mapping names to values, all these are
        overridden in the returned wrapper (after .enabled_wrapper()
        is called)

    :returns: an object that behaves like the original. After
        .enable_wrapper() is called on this object, any attributes found
        in kwargs will be overridden.
    """
    return _DelayedWrap(original, **kwargs)
