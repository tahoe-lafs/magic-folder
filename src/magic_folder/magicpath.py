import re
import os.path

from pyutil.assertutil import precondition, _assert


def mangle_path_segments(segments):
    """
    Perform name-flattening on a list of path segments. Each element
    represents one segment of a path (e.g. (sub)directory or the
    actual filename) and is unicode.

    The mangling consists of replacing path-separators with '@_' and
    escaping '@' symbols inside a path-segment as '@@'.

    :param list[unicode] segments: 1 or more unicode path segments

    :returns unicode: flattened / mangled name
    """
    return u'@_'.join(
        segment.replace(u'@', u'@@')
        for segment in segments
    )


def unmangle_relative_path(mangled_path):
    """
    Undo the name-mangling achieved by `mangle_path_segments`. That
    is, split on `@_` and turn `@@` inside a segment back into `@`.

    :returns unicode: the un-mangled relative path separated by "/".
    """
    def replace(match):
        return {
            u'@_': u'/',
            u'@@': u'@',
        }[match.group(0)]
    return re.sub(u'@[_@]', replace, mangled_path)


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
