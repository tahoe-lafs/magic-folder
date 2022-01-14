"""
Utilities for interacting with Tahoe capability-strings

Tahoe-LAFS internally stores URIs as byte-strings.
Magic Folders uses text ("str" on Python3) to store Tahoe URIs.

These APIs convert for use with the imported Tahoe-LAFS URI interaction code so always use the Magic Folders format when calling the APIs and expect the same to be returned.
"""

from allmydata.uri import (
    IDirectoryURI,
    IDirnodeURI,
    IFileURI,
    IImmutableFileURI,
    IReadonlyDirectoryURI,
    IVerifierURI,
)
from allmydata.uri import from_string as tahoe_uri_from_string


def capability_size(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    :returns int: the size, in bytes, the capability points to
    """
    uri = tahoe_uri_from_string(capability.encode("ascii"))
    if IDirnodeURI.providedBy(uri):
        return uri.get_filenode_cap().get_size()
    return uri.get_size()


def is_directory_cap(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    :returns bool: True if `capability` is a directory-cap of any sort
    """
    uri = tahoe_uri_from_string(capability.encode("ascii"))
    return IDirnodeURI.providedBy(uri)


def is_file_cap(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    :returns bool: True if `capability` is a mutable or immutable file
        capability (note this excludes all kinds of "verify"
        capabilities).
    """
    uri = tahoe_uri_from_string(capability.encode("ascii"))
    return IFileURI.providedBy(uri) or IImmutableFileURI.providedBy(uri)


def is_immutable_directory_cap(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    :returns bool: True if `capability` is an immmutable directory capability
        (note this excludes all kinds of "verify" capabilities).
    """
    uri = tahoe_uri_from_string(capability.encode("ascii"))
    return (
        IDirnodeURI.providedBy(uri)
        and not uri.is_mutable()
        and not IVerifierURI.providedBy(uri)
    )


def is_mutable_directory_cap(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    :returns bool: True if `capability` is a mutable directory capability
        (note this excludes all kinds of "verify" capabilities).
    """
    uri = tahoe_uri_from_string(capability.encode("ascii"))
    return IDirectoryURI.providedBy(uri)


def is_readonly_directory_cap(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    :returns bool: True if `capability` is a read-only directory capability
        (note this excludes all kinds of "verify" capabilities).
    """
    uri = tahoe_uri_from_string(capability.encode("ascii"))
    return IReadonlyDirectoryURI.providedBy(uri)


def to_readonly_capability(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    Converts a capability-string to a readonly capability-string. This
    may be the very same string if it is already read-only.

    :returns str: a read-only version of the URI (could be the same URI)
    """
    cap = tahoe_uri_from_string(capability.encode("ascii"))
    if cap.is_readonly():
        return capability
    return cap.get_readonly().to_string().decode("ascii")


def to_verify_capability(capability):
    """
    :param str capability: a Tahoe-LAFS Capability URI.

    Converts a capability-string to a verify capability-string.

    :returns str: a verify-only version of the URI (could be the same URI)
    """
    verify = tahoe_uri_from_string(capability.encode("ascii")).get_verify_cap()
    return verify.to_string().decode("ascii")
