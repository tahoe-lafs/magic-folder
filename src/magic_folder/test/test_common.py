# Copyright (c) Least Authority TFA GmbH.
# See COPYING.* for details.

"""
Tests for ``magic_folder.test.common``.
"""

import os
import os.path
from stat import (
    S_IWUSR,
    S_IWGRP,
    S_IWOTH,
)

from unittest import (
    TestCase,
)

from .common import (
    SyncTestCase,
)

from twisted.python.filepath import (
    Permissions,
)

def get_synctestcase():
    # Hide this in a function so the test runner doesn't discover it and try
    # to run it.
    class Tests(SyncTestCase):
        def test_foo(self):
            pass
    return Tests("test_foo")

class SyncTestCaseTests(TestCase):
    """
    Tests for ``magic_folder.test.common.SyncTestCase``.
    """
    def test_mktemp_bytes(self):
        """
        ``SyncTestCase.mktemp`` returns ``bytes``.
        """
        tmp = get_synctestcase().mktemp()
        self.assertTrue(
            isinstance(tmp, bytes),
            "Expected bytes but {!r} is instance of {}".format(
                tmp,
                type(tmp),
            ),
        )

    def test_mktemp_identifies_case(self):
        """
        ``SyncTestCase.mktemp`` returns a path associated with the selected test.
        """
        tmp = get_synctestcase().mktemp()
        actual_segments = tmp.split(os.sep)
        case_segments = [
            "magic_folder",
            "test",
            "test_common",
            "Tests",
            "test_foo",
        ]
        self.assertTrue(
            is_sublist(case_segments, actual_segments),
            "Expected to find {!r} in temporary path {!r}".format(
                case_segments,
                actual_segments,
            ),
        )

    def test_parent_is_directory(self):
        """
        ``SyncTestCase.mktemp`` returns a path the parent of which exists and is a
        directory.
        """
        tmp = get_synctestcase().mktemp()
        self.assertTrue(
            os.path.isdir(os.path.split(tmp)[0]),
            "Expected parent of {!r} to be a directory".format(tmp),
        )

    def test_parent_writeable(self):
        """
        ``SyncTestCase.mktemp`` returns a path the parent of which is writeable
        only by the owner.
        """
        tmp = get_synctestcase().mktemp()
        stat = os.stat(os.path.split(tmp)[0])
        self.assertEqual(
            stat.st_mode & (S_IWUSR | S_IWGRP | S_IWOTH),
            S_IWUSR,
            "Expected parent permissions to allow only owner writes, "
            "got {} instead.".format(
                Permissions(stat.st_mode),
            ),
        )

    def test_does_not_exist(self):
        """
        ``SyncTestCase.mktemp`` returns a path which does not exist.
        """
        tmp = get_synctestcase().mktemp()
        self.assertFalse(
            os.path.exists(tmp),
            "Expected {!r} not to exist".format(tmp),
        )


def is_sublist(needle, haystack):
    """
    Determine if a list exists as a sublist of another list.

    :param list needle: The list to seek.
    :param list haystack: The list in which to seek.
    :return bool: ``True`` if and only if ``needle`` is a sublist of
        ``haystack``.
    """
    for i in range(len(haystack) - len(needle)):
        if needle == haystack[i:i + len(needle)]:
            return True
    return False
