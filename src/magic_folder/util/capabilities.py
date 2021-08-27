"""
Utilities for interacting with Tahoe capability-strings
"""

from __future__ import absolute_import, division, print_function, unicode_literals

from allmydata.uri import (
    IDirectoryURI,
    IDirnodeURI,
    IFileURI,
    IImmutableFileURI,
    IReadonlyDirectoryURI,
    IVerifierURI,
)
from allmydata.uri import from_string as tahoe_uri_from_string


def cap_size(capability):
    """
    :returns: the size, in bytes, of the capability
    """
    uri = tahoe_uri_from_string(capability)
    if IDirnodeURI.providedBy(uri):
        return uri.get_filenode_cap().get_size()
    return uri.get_size()


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


def is_immutable_directory_cap(capability):
    """
    :returns bool: True if `capability` is an immmutable directory capability
        (note this excludes all kinds of "verify" capabilities).
    """
    uri = tahoe_uri_from_string(capability)
    return (
        IDirnodeURI.providedBy(uri)
        and not uri.is_mutable()
        and not IVerifierURI.providedBy(uri)
    )


def is_mutable_directory_cap(capability):
    """
    :returns bool: True if `capability` is a mutable directory capability
        (note this excludes all kinds of "verify" capabilities).
    """
    uri = tahoe_uri_from_string(capability)
    return IDirectoryURI.providedBy(uri)


def is_readonly_directory_cap(capability):
    """
    :returns bool: True if `capability` is a read-only directory capability
        (note this excludes all kinds of "verify" capabilities).
    """
    uri = tahoe_uri_from_string(capability)
    return IReadonlyDirectoryURI.providedBy(uri)


def to_readonly_capability(capability):
    """
    Converts a capability-string to a readonly capability-string. This
    may be the very same string if it is already read-only.
    """
    cap = tahoe_uri_from_string(capability)
    if cap.is_readonly():
        return capability
    return cap.get_readonly().to_string()


def to_verify_capability(capability):
    """
    Converts a capability-string to a verify capability-string.
    """
    return tahoe_uri_from_string(capability).get_verify_cap().to_string()
