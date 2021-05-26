import re
import os.path

from pyutil.assertutil import precondition, _assert


_DEMANGLER = re.compile(u'@[_@]')


def path_to_label(base, path):
    """
    :param FilePath base: the base directory (for example, the "magic
        folder dir")

    :param FilePath path: the actual file (for example, a local file
        we're turning into a Snapshot). Must be child if `base`.

    :returns unicode: the relative path from `base` to `path`
        "mangled" into a Snapshot name. It is an error if `path` is
        not a child of `base`. The mangled label can be turned back
        into a FilePath using `label_to_path.
    """
    segments = path.segmentsFrom(base)
    return u'@_'.join(
        segment.replace(u'@', u'@@')
        for segment in segments
    )


def label_to_path(base, mangled_name):
    """
    :param FilePath base: the base directory below which the
        un-mangled name will exist.

    :param unicode mangled_name: the output of a previous call to
        `path_to_label`, this encodes a relative path.

    :returns FilePath: an absolute FilePath below `base` matching this
       manged name. Will raise InsecurePath if the path would end up
       above `base`.
    """

    def replace(match):
        return {
            u'@_': u'/',
            u'@@': u'@',
        }[match.group(0)]
    relpath = _DEMANGLER.sub(replace, mangled_name)
    return base.preauthChild(relpath)


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
