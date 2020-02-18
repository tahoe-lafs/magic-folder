# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Hypothesis strategies useful for testing Magic Folder.
"""

from os.path import (
    join,
)

from base64 import (
    urlsafe_b64encode,
)

from hypothesis.strategies import (
    characters,
    text,
    lists,
    builds,
    binary,
)

from allmydata.util import (
    base32,
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


def folder_names():
    """
    Build unicode strings which are usable as magic folder names.
    """
    return text(
        min_size=1,
    )

def tahoe_lafs_dir_capabilities():
    """
    Build unicode strings which look like Tahoe-LAFS directory capability strings.
    """
    return builds(
        lambda a, b: u"URI:DIR2:{}:{}".format(base32.b2a(a), base32.b2a(b)),
        binary(min_size=16, max_size=16),
        binary(min_size=32, max_size=32),
    )


def tokens():
    """
    Build byte strings which are usable as Tahoe-LAFS web API authentication
    tokens.
    """
    return binary(
        min_size=32,
        max_size=32,
    ).map(
        urlsafe_b64encode,
    )
