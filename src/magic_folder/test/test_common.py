# Copyright (c) Least Authority TFA GmbH.
# See COPYING.* for details.

"""
Tests for ``magic_folder.test.common``.
"""

import os
import os.path
from sys import (
    modules,
)
from stat import (
    S_IWUSR,
    S_IWGRP,
    S_IWOTH,
)

from unittest import (
    TestCase,
)

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    Not,
    Contains,
    Equals,
)

from .common import (
    SyncTestCase,
    skipIf
)
from ..common import (
    atomic_makedirs,
)

from twisted.python.filepath import (
    Permissions,
    FilePath,
)

from twisted.python import runtime


class SyncTestCaseTests(TestCase):
    """
    Tests for ``magic_folder.test.common.SyncTestCase``.
    """
    def setUp(self):
        self.case = SyncTestCase()

    def test_mktemp_str(self):
        """
        ``SyncTestCase.mktemp`` returns ``str``.
        """
        tmp = self.case.mktemp()
        self.assertTrue(
            isinstance(tmp, str),
            "Expected str but {!r} is instance of {}".format(
                tmp,
                type(tmp),
            ),
        )

    def test_mktemp_identifies_case(self):
        """
        ``SyncTestCase.mktemp`` returns a path associated with the selected test.
        """
        tmp = self.case.mktemp()
        actual_segments = tmp.split(os.sep)
        case_segments = [
            "magic_folder",
            "test",
            "common",
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
        tmp = self.case.mktemp()
        self.assertTrue(
            os.path.isdir(os.path.split(tmp)[0]),
            "Expected parent of {!r} to be a directory".format(tmp),
        )

    @skipIf(runtime.platformType == "win32", "windows does not have unix-like permissions")
    def test_parent_writeable(self):
        """
        ``SyncTestCase.mktemp`` returns a path the parent of which is writeable
        only by the owner.
        """
        tmp = self.case.mktemp()
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
        tmp = self.case.mktemp()
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


class OtherTests(SyncTestCase):
    """
    Tests for other behaviors that don't obviously fit anywhere better.
    """
    def test_allmydata_test_not_loaded(self):
        """
        ``allmydata.test`` makes Hypothesis profile changes that are not
        compatible with our test suite.  If it is loaded, this is a problem.
        Verify that it is not loaded.
        """
        self.assertThat(
            modules,
            Not(Contains("allmydata.test")),
        )


class TestAtomicMakedirs(SyncTestCase):
    """
    Confirm cleanup behavior for atomic_makedirs context-manager
    """

    def test_happy(self):
        """
        on a normal exit atomic_makedirs creates the dir
        """
        temp = FilePath(self.mktemp())
        with atomic_makedirs(temp):
            pass
        self.assertThat(
            temp.exists(),
            Equals(True)
        )

    def test_exception(self):
        """
        Upon error atomic_makedirs should erase the directory
        """
        temp = FilePath(self.mktemp())
        with ExpectedException(RuntimeError, "just testing"):
            with atomic_makedirs(temp):
                raise RuntimeError("just testing")
        self.assertThat(
            temp.exists(),
            Equals(False)
        )
