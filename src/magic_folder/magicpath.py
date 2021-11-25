from __future__ import (
    absolute_import,
    division,
    print_function,
)

import os.path
import re

import attr
from pyutil.assertutil import _assert, precondition


@attr.s(auto_exc=True, frozen=True)
class InvalidMangledPath(Exception):
    path = attr.ib()
    sequence = attr.ib()

    def __str__(self):
        return "Invalid escape sequence {!r} in {!r}.".format(self.sequence, self.path)

def path2magic(path):
    return re.sub(u'[/@]', lambda m: {u'/': u'@_', u'@': u'@@'}[m.group(0)], path)

def magic2path(path):
    try:
        return re.sub(u'@.', lambda m: {u'@_': u'/', u'@@': u'@'}[m.group(0)], path)
    except KeyError as e:
        raise InvalidMangledPath(path, str(e))



IGNORE_SUFFIXES = [u'.backup', u'.tmp', u'.conflict']
IGNORE_PREFIXES = [u'.']

def should_ignore_file(path_u):
    precondition(isinstance(path_u, unicode), path_u=path_u)

    for suffix in IGNORE_SUFFIXES:
        if path_u.endswith(suffix):
            return True

    while path_u != u"":
        oldpath_u = path_u
        path_u, tail_u = os.path.split(path_u)
        if tail_u.startswith(u"."):
            return True
        if path_u == oldpath_u:
            return True  # the path was absolute
        _assert(len(path_u) < len(oldpath_u), path_u=path_u, oldpath_u=oldpath_u)

    return False
