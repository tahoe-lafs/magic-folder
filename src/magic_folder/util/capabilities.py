from __future__ import (
    print_function,
    unicode_literals,
)

"""
Utilities for interacting with Tahoe capability-strings
"""


from allmydata.uri import (
    from_string as tahoe_uri_from_string,
    IDirectoryURI,
)


def is_directory_cap(capability):
    """
    :returns: True if `capability` is a directory-cap of any sort
    """
    uri = tahoe_uri_from_string(capability)
    return IDirectoryURI.providedBy(uri)


def to_readonly_capability(capability):
    """
    Converts a capability-string to a readonly capability-string. This
    may be the very same string if it is already read-only.
    """
    cap = tahoe_uri_from_string(capability)
    if cap.is_readonly():
        return capability
    return cap.get_readonly().to_string()
