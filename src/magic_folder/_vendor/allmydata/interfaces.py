"""
Interfaces for Tahoe-LAFS.

Ported to Python 3.

Note that for RemoteInterfaces, the __remote_name__ needs to be a native string because of https://github.com/warner/foolscap/blob/43f4485a42c9c28e2c79d655b3a9e24d4e6360ca/src/foolscap/remoteinterface.py#L67
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from zope.interface import Interface


class IURI(Interface):
    def init_from_string(uri):
        """Accept a string (as created by my to_string() method) and populate
        this instance with its data. I am not normally called directly,
        please use the module-level uri.from_string() function to convert
        arbitrary URI strings into IURI-providing instances."""

    def is_readonly():
        """Return False if this URI be used to modify the data. Return True
        if this URI cannot be used to modify the data."""

    def is_mutable():
        """Return True if the data can be modified by *somebody* (perhaps
        someone who has a more powerful URI than this one)."""

    # TODO: rename to get_read_cap()
    def get_readonly():
        """Return another IURI instance that represents a read-only form of
        this one. If is_readonly() is True, this returns self."""

    def get_verify_cap():
        """Return an instance that provides IVerifierURI, which can be used
        to check on the availability of the file or directory, without
        providing enough capabilities to actually read or modify the
        contents. This may return None if the file does not need checking or
        verification (e.g. LIT URIs).
        """

    def to_string():
        """Return a string of printable ASCII characters, suitable for
        passing into init_from_string."""


class IVerifierURI(Interface, IURI):
    def init_from_string(uri):
        """Accept a string (as created by my to_string() method) and populate
        this instance with its data. I am not normally called directly,
        please use the module-level uri.from_string() function to convert
        arbitrary URI strings into IURI-providing instances."""

    def to_string():
        """Return a string of printable ASCII characters, suitable for
        passing into init_from_string."""


class IDirnodeURI(Interface):
    """I am a URI that represents a dirnode."""


class IFileURI(Interface):
    """I am a URI that represents a filenode."""
    def get_size():
        """Return the length (in bytes) of the file that I represent."""


class IImmutableFileURI(IFileURI):
    pass

class IMutableFileURI(Interface):
    pass

class IDirectoryURI(Interface):
    pass

class IReadonlyDirectoryURI(Interface):
    pass


class CapConstraintError(Exception):
    """A constraint on a cap was violated."""

class MustBeDeepImmutableError(CapConstraintError):
    """Mutable children cannot be added to an immutable directory.
    Also, caps obtained from an immutable directory can trigger this error
    if they are later found to refer to a mutable object and then used."""

class MustBeReadonlyError(CapConstraintError):
    """Known write caps cannot be specified in a ro_uri field. Also,
    caps obtained from a ro_uri field can trigger this error if they
    are later found to be write caps and then used."""

class MustNotBeUnknownRWError(CapConstraintError):
    """Cannot add an unknown child cap specified in a rw_uri field."""
