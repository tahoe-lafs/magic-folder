# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Hypothesis strategies useful for testing Magic Folder.
"""

from os.path import (
    join,
)

from hypothesis.strategies import (
    characters,
    text,
    lists,
    builds,
)

def path_segments(alphabet=characters(blacklist_characters=[u"/", u"\0"])):
    """
    Build unicode strings which are usable as individual segments in a
    filesystem path.
    """
    return text(
        alphabet=alphabet,
        min_size=1,
        max_size=255,
    ).filter(
        # Exclude aliases for current directory and parent directory.
        lambda segment: segment not in {u".", u".."},
    )

def path_segments_without_dotfiles(path_segments=path_segments()):
    """
    Build unicode strings which are usable as individual segments in a
    filesystem path and which never start with a ``.``.
    """
    return builds(
        lambda a, b: a + b,
        characters(blacklist_characters=[u"/", u"\0", u"."]),
        path_segments,
    )

def relative_paths(segments=path_segments()):
    """
    Build unicode strings which are usable as relative filesystem paths.
    """
    # There is PATH_MAX but it is a bit of a lie.  Set this arbitrarily to
    # limit computational complexity of the strategy.
    return lists(
        segments,
        min_size=1,
        max_size=8,
    ).map(
        lambda xs: join(*xs),
    )


def absolute_paths(relative_paths=relative_paths()):
    """
    Build unicode strings which are usable as absolute filesystem paths.
    """
    return relative_paths.map(
        lambda p: u"/" + p,
    )
