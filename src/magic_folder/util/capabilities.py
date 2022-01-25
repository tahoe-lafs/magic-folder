"""
Utilities for interacting with Tahoe capability-strings

Tahoe-LAFS internally stores URIs as byte-strings.
Magic Folders uses text ("str" on Python3) to store Tahoe URIs.

These APIs convert for use with the imported Tahoe-LAFS URI interaction code so always use the Magic Folders format when calling the APIs and expect the same to be returned.
"""

from allmydata.uri import (
    IURI,
    UnknownURI,
    IDirectoryURI,
    IDirnodeURI,
    IFileURI,
    IImmutableFileURI,
    IReadonlyDirectoryURI,
    ImmutableDirectoryURI,
)
from allmydata.uri import from_string as _tahoe_uri_from_string
import hashlib
from base64 import (
    b32encode,
)
from os import (
    urandom,
)
import attr


@attr.s(frozen=True)
class Capability:
    """
    Represents a Tahoe-LAFS capability (Tahoe-LAFS calls these "URIs").

    Although it might be nice to grow this into the "URI handling
    library" / API that Tahoe-LAFS _doesn't_ have for now it serves to
    at leats wrap capability-use in a real class (instad of a str)
    which allows better typing (e.g. in attrs and elsewhere) and helps
    ensure we don't accidentally print out a capability in logs or
    similar.
    """

    # the original capability-string
    _uri = attr.ib(validator=attr.validators.instance_of(str), repr=False)
    # a Tahoe object representing the capability
    _tahoe_cap = attr.ib(validator=attr.validators.provides(IURI), repr=False)

    @classmethod
    def from_string(cls, capability_string):
        """
        :returns Capability: a new instance from the given capability-string, or an
            exception if it is not well-formed.
        """
        cap = _tahoe_uri_from_string(capability_string)
        if isinstance(cap, UnknownURI):
            raise ValueError(
                "Invalid capability-string {}".format(capability_string)
            )
        return cls(
            uri=capability_string,
            tahoe_cap=cap,
        )

    @property
    def size(self):
        """
        The size, in bytes, of the data this capability represents
        """
        if IDirnodeURI.providedBy(self._tahoe_cap):
            return self._tahoe_cap.get_filenode_cap().get_size()
        return self._tahoe_cap.get_size()

    def is_directory(self):
        """
        :returns bool: True if this is a directory-cap of any sort
        """
        return IDirnodeURI.providedBy(self._tahoe_cap)

    def is_file(self):
        """
        :returns bool: True if this is a mutable or immutable file
            capability (note this excludes all kinds of "verify"
            capabilities).
        """
        return IFileURI.providedBy(self._tahoe_cap) or IImmutableFileURI.providedBy(self._tahoe_cap)

    def is_mutable_directory(self):
        """
        :returns bool: True if this is a mutable directory capability
            (note this excludes all kinds of "verify" capabilities).
        """
        return IDirectoryURI.providedBy(self._tahoe_cap)

    def is_immutable_directory(self):
        """
        :returns bool: True if this is a mutable directory capability
            (note this excludes all kinds of "verify" capabilities).
        """
        return isinstance(self._tahoe_cap, ImmutableDirectoryURI)

    def is_readonly_directory(self):
        """
        :returns bool: True if this is a read-only directory capability
            (note this excludes all kinds of "verify" capabilities).
        """
        return IReadonlyDirectoryURI.providedBy(self._tahoe_cap)

    def to_readonly(self):
        """
        Converts to a read-only Capability. Note that this may be the same
        instance if this is already read-only.

        :returns Capability: a read-only version (could be the same Capability)
        """
        if self._tahoe_cap.is_readonly():
            return self
        return Capability.from_string(
            self._tahoe_cap.get_readonly().to_string().decode("ascii")
        )

    def to_verifier(self):
        """
        Converts to a verify capability.

        :returns Capbility: a verify-only version (which might be the
            same instance if this is already a verify Capability).
        """
        return Capability.from_string(
            self._tahoe_cap.get_verify_cap().to_string().decode("ascii")
        )

    def hex_digest(self):
        """
        :returns str: a hex-encoded sha256 digest of the
            capability-string.
        """
        h = hashlib.sha256()
        h.update(self._uri.encode("ascii"))
        return h.hexdigest()

    def __str__(self):
        """
        Return a SAFE string representation of this capability.

        This should _not_ be the real capability-string but some
        mangled version of it suitable for use in logs, etc. Code that
        requires the real capability-string must use
        danger_real_capability_string()
        """
        return "[REDACTED]"

    def __repr__(self):
        return "<Capability>"

    def danger_real_capability_string(self):
        """
        Return the real capability string.

        This is original str of the capability and usually contains
        secret or sensitive data. We name this method to encourage
        programmers to think about whether the actual
        capability-string is required in such a case or not.

        :returns str: a Tahoe-LAFS URI string
        """
        return self._uri

    def __hash__(self):
        """
        Logically, two capabilities are 'the same' if their actual
        underlying Tahoe-LAFS URI is identical
        """
        return self._uri

    def __eq__(self, other):
        """
        Logically, two capabilities are 'the same' if their actual
        underlying Tahoe-LAFS URI is identical
        """
        return self.__uri__ == other._uri


def random_dircap(readonly=False):
    """
    :returns Capability: a random directory-capability ("URI:DIR2" or "URI:DIR2-RO")
    """
    return Capability.from_string(
        u"{}:{}:{}".format(
            "URI:DIR2-RO" if readonly else "URI:DIR2",
            b32encode(urandom(16)).rstrip(b"=").lower().decode("ascii"),
            b32encode(urandom(32)).rstrip(b"=").lower().decode("ascii"),
        )
    )

def random_immutable(directory=False, needed=2, extra=3, happy=3):
    """
    :returns Capability: a random CHK immutable capability "URI:CHK"
        or if directory is True a "URI:DIR2-CHK:"
    """
    return Capability.from_string(
        u"{}:{}:{}:{}:{}:{}".format(
            "URI:DIR2-CHK" if directory else "URI:CHK",
            b32encode(urandom(16)).rstrip(b"=").lower().decode("ascii"),
            b32encode(urandom(32)).rstrip(b"=").lower().decode("ascii"),
            needed,
            needed + extra,
            happy,
        )
    )
