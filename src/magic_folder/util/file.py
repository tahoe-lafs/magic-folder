# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Utilties for dealing with on disk files.
"""

import os
import stat
from errno import ENOENT

import attr
from twisted.python.filepath import FilePath


@attr.s(frozen=True, order=False)
class PathState(object):
    """
    The filesystem information we use to check if a file has changed.
    """

    size = attr.ib(validator=attr.validators.instance_of((int, type(None))))
    mtime_ns = attr.ib(validator=attr.validators.instance_of((int, type(None))))
    ctime_ns = attr.ib(validator=attr.validators.instance_of((int, type(None))))


@attr.s(frozen=True, order=False)
class PathInfo(object):
    """
    The stat information of a file on disk.
    """

    is_dir = attr.ib(validator=attr.validators.instance_of(bool))
    is_file = attr.ib(validator=attr.validators.instance_of(bool))
    is_link = attr.ib(validator=attr.validators.instance_of(bool))
    exists = attr.ib(validator=attr.validators.instance_of(bool))
    state = attr.ib(
        validator=attr.validators.optional(attr.validators.instance_of(PathState))
    )


def seconds_to_ns(t):
    return int(t * 1000000000)


def ns_to_seconds(t):
    """
    :param int t: nanoseconds
    :returns int: the seconds representation of 't'
    """
    if t is None:
        return None
    return int(t) // 1000000000


def ns_to_seconds_float(t):
    """
    :param float t: nanoseconds
    :returns float: the seconds representation of 't'
    """
    if t is None:
        return None
    return float(t) / 1000000000.0


def get_pathinfo(path):
    # type: (FilePath) -> PathInfo
    try:
        statinfo = os.lstat(path.path)
        mode = statinfo.st_mode
        is_file = stat.S_ISREG(mode)
        if is_file:
            path_state = PathState(
                size=statinfo.st_size,
                mtime_ns=seconds_to_ns(statinfo.st_mtime),
                ctime_ns=seconds_to_ns(statinfo.st_ctime),
            )
        else:
            path_state = None
        return PathInfo(
            is_dir=stat.S_ISDIR(mode),
            is_file=is_file,
            is_link=stat.S_ISLNK(mode),
            exists=True,
            state=path_state,
        )
    except OSError as e:
        if e.errno == ENOENT:
            return PathInfo(
                is_dir=False,
                is_file=False,
                is_link=False,
                exists=False,
                state=None,
            )
        raise
