# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Utilties for dealing with on disk files.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os
import stat
from errno import ENOENT

import attr
from six import integer_types
from twisted.python.filepath import FilePath


@attr.s(frozen=True, order=False)
class PathState(object):
    """
    The filesystem information we use to check if a file has changed.
    """

    size = attr.ib(validator=attr.validators.instance_of(integer_types))
    mtime_ns = attr.ib(validator=attr.validators.instance_of(integer_types))
    ctime_ns = attr.ib(validator=attr.validators.instance_of(integer_types))


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


def get_pathinfo(path):
    # type: (FilePath) -> PathInfo
    try:
        statinfo = os.lstat(path.asBytesMode("utf-8").path)
        mode = statinfo.st_mode
        is_file = stat.S_ISREG(mode)
        if is_file:
            path_state = PathState(
                size=statinfo.st_size,
                mtime_ns=seconds_to_ns(statinfo.st_mtime),
                ctime_ns=seconds_to_ns(statinfo.st_ctime),
            )
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
                isdir=False,
                isfile=False,
                islink=False,
                exists=False,
                state=None,
            )
        raise
