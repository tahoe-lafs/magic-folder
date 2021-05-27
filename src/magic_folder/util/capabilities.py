from __future__ import (
    print_function,
    unicode_literals,
)

"""
Utilities for interacting with Tahoe capability-strings
"""


from allmydata.uri import (
    from_string as tahoe_uri_from_string,
    IDirnodeURI,
    IFileURI,
    IImmutableFileURI,
)


def is_directory_cap(capability):
    """
    :returns: True if `capability` is a directory-cap of any sort
    """
    uri = tahoe_uri_from_string(capability)
    return IDirnodeURI.providedBy(uri)


def is_file_cap(capability):
    """
    :returns bool: True if `capability` is a mutable or immutable file
        capability (note this excludes all kinds of "verify"
        capabilities).
    """
    uri = tahoe_uri_from_string(capability)
    return IFileURI.providedBy(uri) or IImmutableFileURI.providedBy(uri)


def to_readonly_capability(capability):
    """
    Converts a capability-string to a readonly capability-string. This
    may be the very same string if it is already read-only.
    """
    cap = tahoe_uri_from_string(capability)
    if cap.is_readonly():
        return capability
    return cap.get_readonly().to_string()
