# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Tests for ``magic_folder.magicpath``.
"""

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

from testtools.matchers import (
    AfterPreprocessing,
    Equals,
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
    path2magic,
    magic2path,
    should_ignore_file,
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

    @given(relative_paths(), sampled_from([u"backup", u"tmp", u"conflict"]))
    def test_ignore_known_suffixes(self, path, suffix):
        """
        Relative paths ending with certain well-known suffixes are ignored.
        """
        path += u"." + suffix
        self.assertThat(
            path,
            AfterPreprocessing(should_ignore_file, Equals(True)),
        )

    @given(randoms(), lists(path_segments(), min_size=1))
    def test_ignore_dotfiles(self, random, path_segments):
        """
        Relative paths involving dotfiles are ignored.
        """
        index = random.randrange(len(path_segments))
        path_segments[index] = u"." + path_segments[index]
        path = join(*path_segments)
        self.assertThat(
            path,
            AfterPreprocessing(should_ignore_file, Equals(True)),
        )

    @given(absolute_paths())
    def test_ignore_absolute_paths(self, path):
        """
        Absolute paths are ignored.
        """
        self.assertThat(
            path,
            AfterPreprocessing(should_ignore_file, Equals(True)),
        )

    @given(relative_paths(path_segments_without_dotfiles()))
    def test_dont_ignore_others(self, path):
        """
        Relative paths not involving dotfiles and without certain well-known
        suffixes are not ignored.
        """
        assume(u"/." not in path)
        assume(
            not any(
                path.endswith(suffix)
                for suffix
                in [u".backup", u".tmp", u".conflict"]
            )
        )
        self.assertThat(
            path,
            AfterPreprocessing(should_ignore_file, Equals(False)),
        )
