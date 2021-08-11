# Copyright (c) Least Authority TFA GmbH.

"""
Utilities for interacting with twisted.
"""


from __future__ import absolute_import, division, print_function, unicode_literals

from functools import wraps


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
