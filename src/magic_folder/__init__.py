# Copyright (C) Least Authority TFA GmbH

__all__ = [
    "__version__",
]

from ._version import (
    __version__,
)

def _monkeypatch_filesystemencoding():
    """
    Monkey-patch ``sys.getfilesystemencoding`` with a version that always
    returns UTF-8.

    The stdlib implementation of the function always returns UTF-8 on macOS
    and Windows.  On Linux, it returns a value determined by the active locale
    for the process.  This will usually be UTF-8 because of the wide
    applicability of UTF-8.  The next most common value is ASCII when there is
    no active locale or "C" or "POSIX" is active.  This usually comes about
    because the process is running in a context more likely to be headless and
    locale settings are typically determined by a user session or other
    user-specific settings.  In principle it is also possible to have other
    locales active based on other user-specific settings.

    The filesystem encoding *must* be one which can represent all unicode code
    points or Magic-Folder daemon interactions with the filesystem will break
    when non-representable code points are encountered (in folder names, in
    contained file names, in the working directory, etc).

    Python eventually acknowledged this (around 3.6 or 3.7) changed
    ``sys.getfilesystemencoding`` to always return UTF-8 as well.  So in some
    sense we're just backporting this fix.
    """
    import sys
    def utf8filesystemencoding():
        return "UTF-8"
    sys.getfilesystemencoding = utf8filesystemencoding
_monkeypatch_filesystemencoding()
