# Copyright (C) Least Authority TFA GmbH

__all__ = [
    "__version__",
]

from ._version import (
    __version__,
)

def _set_filesystemencoding():
    """
    Change Python's idea of the "filesystem encoding" to UTF-8.

    The interpreter setup always makes this UTF-8 on macOS and Windows.  On
    Linux, it picks a value determined by the active locale for the process.
    This will usually be UTF-8 because of the wide applicability of UTF-8.
    The next most common value is ASCII when there is no active locale or "C"
    or "POSIX" is active.  This usually comes about because the process is
    running in a context more likely to be headless and locale settings are
    typically determined by a user session or other user-specific settings.
    In principle it is also possible to have other locales active based on
    other user-specific settings.

    The filesystem encoding *must* be one which can represent all unicode code
    points or Magic-Folder daemon interactions with the filesystem will break
    when non-representable code points are encountered (in folder names, in
    contained file names, in the working directory, etc).

    Python eventually acknowledged this (around 3.6 or 3.7) and changed to
    always use UTF-8 (except when explicitly overridden).  So in some sense
    we're just backporting this fix.

    We can't just monkey-patch the Python API, ``sys.getfilesystemencoding``,
    because the large body of stdlib filesystem functionality implemented in C
    ignores this Python API and uses the C symbol's value directly.

    We also can't just reach into the C ABI and replace the value of the C
    symbol (Py_FileSystemDefaultEncoding) because it's hard to *find* that
    symbol in a cross-platform, cross-version manner and because PyPy doesn't
    have such a symbol.

    Thus, if necessary, we just re-start the process with LANG set to
    something that should result in UTF-8.
    """

    # First of all, if we don't have to do this, don't.
    import sys
    if sys.getfilesystemencoding() == "UTF-8":
        return

    locale_name = "C.UTF-8"

    import os
    if os.environ.get("LANG") == locale_name:
        # Our trick doesn't work.  Avoid going into an infinite execv loop.
        raise RuntimeError(
            "Found filesystem encoding {!r} but LANG={!r} "
            "so I don't know how to fix it.".format(
                sys.getfilesystemencoding(),
                os.environ["LANG"],
            ),
        )

    # sys.argv[0] == "" when the interpreter is run "interactively".
    executable = sys.argv[0] or sys.executable
    os.environ["LANG"] = locale_name
    print("Re-executing {!r} {!r}".format(executable, sys.argv))
    os.execv(
        executable,
        sys.argv,
    )

_set_filesystemencoding()
