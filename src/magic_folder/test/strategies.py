# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Hypothesis strategies useful for testing Magic Folder.
"""

from os.path import (
    join,
)

from unicodedata import (
    normalize,
)

from base64 import (
    urlsafe_b64encode,
)

from hypothesis.strategies import (
    just,
    booleans,
    characters,
    text,
    lists,
    builds,
    binary,
    integers,
    fixed_dictionaries,
)

from allmydata.util import (
    base32,
)
from allmydata.uri import (
    from_string as cap_from_string,
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
    ).map(
        _normalized,
    )

def _normalized(text):
    # In the future we should generate text using different normalizations and
    # denormalized.  The user is likely to be able to enter anything they
    # want, we should know what our behavior is going to be.
    #
    # https://github.com/LeastAuthority/magic-folder/issues/36
    return normalize("NFC", text)


def tahoe_lafs_chk_capabilities():
    """
    Build unicode strings which look like Tahoe-LAFS CHK capability strings.
    """
    return builds(
        lambda a, b, needed, extra, size: u"URI:CHK:{}:{}:{}:{}:{}".format(
            base32.b2a(a),
            base32.b2a(b),
            needed,
            # Total is how many you need plus how many more there might be.
            needed + extra,
            size,
        ),
        binary(min_size=16, max_size=16),
        binary(min_size=32, max_size=32),
        integers(min_value=1, max_value=128),
        integers(min_value=0, max_value=127),
        integers(min_value=56),
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


def filenodes():
    """
    Build JSON-compatible descriptions of Tahoe-LAFS filenode metadata.
    """
    return fixed_dictionaries({
        "ro_uri": tahoe_lafs_chk_capabilities().map(
            lambda cap_text: cap_from_string(
                cap_text.encode("ascii"),
            ).get_readonly(
            ).to_string(
            ).decode(
                "ascii",
            ),
        ),
        "size": integers(min_value=0),
        "format": just(u"CHK"),
        "metadata": fixed_dictionaries({
            "version": integers(min_value=0),
            "deleted": booleans(),
            "tahoe": fixed_dictionaries({
                "linkmotime": integers(min_value=0),
                "linkcrtime": integers(min_value=0),
            }),
        }),
    })
