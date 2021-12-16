# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Tests for ``magic_folder.magicpath``.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)

from os.path import (
    join,
)

from hypothesis import (
    given,
    assume,
)
from hypothesis.strategies import (
    sampled_from,
    lists,
    randoms,
)

from testtools import ExpectedException
from testtools.matchers import (
    AfterPreprocessing,
    Equals,
    StartsWith,
)

from .common import (
    SyncTestCase,
)

from .strategies import (
    path_segments,
    path_segments_without_dotfiles,
    relative_paths,
    absolute_paths,
)

from ..magicpath import (
    InvalidMangledPath,
    path2magic,
    magic2path,
)


class MagicPath(SyncTestCase):
    """
    Tests for handling of paths related to the contents of Magic Folders.
    """
    @given(relative_paths())
    def test_roundtrip(self, path):
        """
        magic2path(path2magic(p)) == p
        """
        self.assertThat(
            magic2path(path2magic(path)),
            Equals(path),
        )

    def test_invalid(self):
        with ExpectedException(InvalidMangledPath):
            magic2path("@metadata")

    def test_invalid_exception_str(self):
        """
        confirm the __str__ method of InvalidMangledPath doesn't fail
        """
        self.assertThat(
            str(InvalidMangledPath("@invalid", "sequence error")),
            StartsWith("Invalid escape sequence")
        )
