"""
Utilities for interacting with Tahoe capability-strings

Tahoe-LAFS internally stores URIs as byte-strings.
Magic Folders uses text ("str" on Python3) to store Tahoe URIs.

These APIs convert for use with the imported Tahoe-LAFS URI interaction code so always use the Magic Folders format when calling the APIs and expect the same to be returned.
"""

import tahoe_capabilities as _caplib

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
    # a Tahoe-Capabilities object representing the capability
    _cap: _caplib.Capability = attr.ib(repr=False)

    @classmethod
    def from_string(cls, capability_string: str) -> "Capability":
        """
        :raise ValueError: If the capability string is not well-formed or
            is otherwise unparseable.

        :returns: a new instance from the given capability-string.
        """
        cap = _caplib.capability_from_string(capability_string)
        return cls(
            uri=capability_string,
            cap=cap,
        )

    @classmethod
    def from_capability(cls, cap: _caplib.Capability) -> "Capability":
        return cls(
            uri=_caplib.danger_real_capability_string(cap),
            cap=cap,
        )

    @property
    def size(self) -> int:
        """
        The size, in bytes, of the data this capability represents

        :raise TypeError: if the underlying capability type does not have a
            fixed size.
        """
        cap = self._cap
        if _caplib.is_directory(cap):
            # Check on the underlying capability
            cap = self._cap.cap_object

        try:
            return cap.size
        except AttributeError:
            raise TypeError(f"Capability type {self._cap.prefix} has no fixed size")

    def is_directory(self) -> bool:
        """
        :returns: True if this is a directory-cap of any sort (note
            this excludes all kinds of "verify" capabilities)
        """
        return _caplib.is_directory(self._cap) and not self.is_verify()

    def is_file(self):
        """
        :returns: True if this is a mutable or immutable file
            capability (note this excludes all kinds of "verify"
            capabilities).
        """
        return not _caplib.is_directory(self._cap) and not self.is_verify()

    def is_mutable_directory(self) -> bool:
        """
        :returns: True if this is a mutable directory capability
            (note this excludes all kinds of "verify" capabilities).
        """
        return self.is_mutable() and self.is_directory()

    def is_immutable_directory(self) -> bool:
        """
        :returns: True if this is a mutable directory capability
            (note this excludes all kinds of "verify" capabilities).
        """
        return not self.is_mutable() and self.is_directory()

    def is_readonly_directory(self) -> bool:
        """
        :returns: True if this is a read-only directory capability
            (note this excludes all kinds of "verify" capabilities).
        """
        return self.is_readonly() and self.is_directory()

    def is_verify(self):
        return _caplib.is_verify(self._cap)

    def is_mutable(self):
        return _caplib.is_mutable(self._cap)

    def is_readonly(self):
        return _caplib.is_read(self._cap)

    def to_readonly(self) -> "Capability":
        """
        Converts to a read-only Capability. Note that this may be the same
        instance if this is already read-only.

        :returns Capability: a read-only version (could be the same Capability)
        """
        if self.is_verify():
            raise ValueError(f"Capability type {self._cap.prefix} cannot be converted to read-only.")
        if self.is_readonly():
            return self
        return Capability.from_capability(self._cap.reader)

    def to_verifier(self) -> "Capability":
        """
        Converts to a verify capability.

        :returns: a verify-only version (which might be the
            same instance if this is already a verify Capability).
        """
        if self.is_verify():
            return self
        if self.is_readonly():
            # We can perhaps read the verifier for this cap directly.
            cap = self._cap
        else:
            # It is neither verify nor readonly, it is probably a write cap.
            # We can read a verifier from its reader.
            cap = self._cap.reader

        try:
            v = cap.verifier
        except AttributeError:
            raise TypeError(f"Capability type {self._cap.prefix} has no verifier")

        return Capability.from_capability(v)

    def hex_digest(self) -> str:
        """
        :returns: a hex-encoded sha256 digest of the
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
