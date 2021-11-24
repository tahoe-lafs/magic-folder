


class _Wrap(object):
    """
    A wrapper class that directs calls to a child object, except for
    explicit overrides.
    """

    def __init__(self, original, **kwargs):
        self._original = original
        self._overrides = kwargs

    def __getattr__(self, name):
        over = self._overrides.get(name, None)
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
