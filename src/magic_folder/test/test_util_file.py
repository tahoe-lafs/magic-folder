# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Utilties for dealing with files.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os
import time

from testtools.matchers import (
    AfterPreprocessing,
    Equals,
    IsInstance,
    LessThan,
    MatchesAll,
    MatchesStructure,
    Not,
)
from twisted.python import runtime
from twisted.python.filepath import FilePath

from ..util.file import PathInfo, PathState, get_pathinfo, ns_to_seconds
from .common import SyncTestCase, skipIf


class PathInfoTests(SyncTestCase):
    def test_dir(self):
        """
        :py:`get_pathinfo` returns a :py:`PathInfo` when given a directory.
        """
        path = FilePath(self.mktemp())
        path.createDirectory()
        path_info = get_pathinfo(path)
        self.assertThat(
            path_info,
            MatchesStructure.byEquality(
                is_dir=True,
                is_file=False,
                is_link=False,
                exists=True,
                state=None,
            ),
        )

    @skipIf(
        runtime.platformType == "win32", "windows does not have unprivileged symlinks"
    )
    def test_symlink(self):
        """
        :py:`get_pathinfo` returns a :py:`PathInfo` when given a symlink.
        """
        dest = FilePath(self.mktemp())
        dest.setContent("content")
        path = FilePath(self.mktemp())
        dest.linkTo(path)
        path_info = get_pathinfo(path)
        self.assertThat(
            path_info,
            MatchesStructure.byEquality(
                is_dir=False,
                is_file=False,
                is_link=True,
                exists=True,
                state=None,
            ),
        )

    @skipIf(runtime.platformType == "win32", "windows does not have named pipe files")
    def test_fifo(self):
        """
        :py:`get_pathinfo` returns a :py:`PathInfo` when given a named pipe.
        """
        path = FilePath(self.mktemp())
        os.mkfifo(path.path)
        path_info = get_pathinfo(path)
        self.assertThat(
            path_info,
            MatchesAll(
                IsInstance(PathInfo),
                MatchesStructure.byEquality(
                    is_dir=False,
                    is_file=False,
                    is_link=False,
                    exists=True,
                    state=None,
                ),
            ),
        )

    def test_non_existant(self):
        """
        :py:`get_pathinfo` returns a :py:`PathInfo` when given path that does
        not exist.
        """
        path = FilePath(self.mktemp())
        path_info = get_pathinfo(path)
        self.assertThat(
            path_info,
            MatchesAll(
                IsInstance(PathInfo),
                MatchesStructure.byEquality(
                    is_dir=False,
                    is_file=False,
                    is_link=False,
                    exists=False,
                    state=None,
                ),
            ),
        )

    def test_file(self):
        """
        :py:`get_pathinfo` returns a :py:`PathInfo` when given regulare file.
        """
        now = int(time.time())
        match_after_now = AfterPreprocessing(
            ns_to_seconds,
            Not(LessThan(now)),
        )

        content = "content"
        path = FilePath(self.mktemp())
        path.setContent(content)
        path_info = get_pathinfo(path)

        self.assertThat(
            path_info,
            MatchesAll(
                IsInstance(PathInfo),
                MatchesStructure(
                    is_dir=Equals(False),
                    is_file=Equals(True),
                    is_link=Equals(False),
                    exists=Equals(True),
                    state=MatchesAll(
                        IsInstance(PathState),
                        MatchesStructure(
                            size=Equals(len(content)),
                            mtime_ns=match_after_now,
                            ctime_ns=match_after_now,
                        ),
                    ),
                ),
            ),
        )
