# Copyright (C) Least Authority TFA GmbH

__all__ = [
    "__version__",
]

from ._version import (
    __version__,
)

def _set_filesystemencoding():
    """
    Change the value of ``Py_FileSystemDefaultEncoding`` to UTF-8.

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

    Python eventually acknowledged this (around 3.6 or 3.7) and changed
    ``Py_FileSystemDefaultEncoding`` to always be UTF-8.  So in some sense
    we're just backporting this fix.

    We can't just monkey-patch the Python API, ``sys.getfilesystemencoding``,
    because the large body of stdlib filesystem functionality implemented in C
    ignores this Python API and uses the C symbol's value directly.
    """
    # First of all, if we don't have to do this, don't.
    import sys
    if sys.getfilesystemencoding() == "UTF-8":
        return

    # We have to keep our "UTF-8" string alive by keeping a reference to it
    # for the whole process lifetime.
    global _UTF8

    from os.path import join
    import sysconfig
    import cffi


    # Define the C ABI we want to interact with.
    ffi = cffi.FFI()
    ffi.cdef("extern char* Py_FileSystemDefaultEncoding;")

    # Locate and open the Python shared library.
    libpython = join(
        sysconfig.get_config_var("LIBDIR"),
        sysconfig.get_config_var("LDLIBRARY"),
    )
    lib = ffi.dlopen(libpython)

    # Allocate our UTF-8 string and stash a reference to so it is kept alive.
    _UTF8 = ffi.new("char[]", "UTF-8")

    # Replace the platform value.
    lib.Py_FileSystemDefaultEncoding = _UTF8

    if sys.getfilesystemencoding() != "UTF-8":
        raise RuntimeError("Failed to change Python's filesystem encoding to UTF-8.")

_set_filesystemencoding()
