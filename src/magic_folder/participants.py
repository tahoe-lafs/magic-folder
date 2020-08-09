# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functionality to interact with the participants in a magic folder.
"""

from zope.interface import (
    Attribute,
    Interface,
    implementer,
)

import attr

from twisted.internet.defer import (
    returnValue,
)

from allmydata.interfaces import (
    IDirectoryURI,
)
from allmydata.uri import (
    from_string as tahoe_uri_from_string,
)
from allmydata.util.eliotutil import (
    inline_callbacks,
)

from .magicpath import (
    magic2path,
)

class IParticipant(Interface):
    """
    An ``IParticipant`` provider represents a single participant in a
    particular magic folder.
    """
    is_self = Attribute("``True`` if this participant is us, ``False`` otherwise.")

    def files():
        """
        Get all of the files this participant has uploaded to this magic folder.

        :return Deferred[dict[unicode, FolderFile]]: All of the contents
            belonging to this participant.  The keys are relative paths and
            the associated values provide some details about the contents at
            those paths.
        """


class IParticipants(Interface):
    """
    An ``IParticipants`` provider grants access to the group of other
    participants in a particular magic folder.
    """
    def list():
        """
        Get all of the participants.

        :return Deferred[[IParticipant]]: A ``Deferred`` that fires with a
            ``list`` of ``IParticipant`` providers representing all of the
            participants in the particular magic folder this object is
            associated with..
        """


def participant_from_dmd(name, dirnode, is_self):
    """
    Create an ``IParticipant`` provider backed by the DMD at the given
    location.

    :param unicode name: The nickname of this participant.

    :param IDirectoryNode dirnode: The Tahoe-LAFS directory node that holds
        this participant's state.

    :param bool is_self: ``True`` if we know this participant represents
        ourself in the magic folder, ``False`` otherwise.

    :return IParticipant: A participant object for accessing this
        participant's state.
    """
    return _CollectiveDirnodeParticipant(name, dirnode, is_self)


def participants_from_collective(tahoe_client, collective_dirnode, upload_dirnode):
    """
    Get an ``IParticipants`` provider that reads participants from the given
    Tahoe-LAFS dirnodes.

    :param TahoeClient tahoe_client: access to our Tahoe-LAFS node

    :param bytes collective_dircap: The magic folder "collective"
        directory into which participant DMDs are linked.

    :param bytes upload_dircap: The DMD for ourself, used to
        identify which participant is ourself.

    :return: An ``IParticipants`` provider.
    """
    return _CollectiveDirnodeParticipants(collective_dirnode, upload_dirnode)


@implementer(IParticipants)
@attr.s(frozen=True)
class _CollectiveDirnodeParticipants(object):
    _collective_dirnode = attr.ib()
    _upload_dirnode = attr.ib()

    @_collective_dirnode.validator
    def readonly_dirnode(self, attribute, value):
        uri = tahoe_uri_from_string(value)
        if IDirectoryURI.providedBy(uri):
            return
        raise TypeError(
            "Collective dirnode was {!r}, must be a directory node.".format(
                value,
            ),
        )

    @_upload_dirnode.validator
    def mutable_dirnode(self, attribute, value):
        uri = tahoe_uri_from_string(value)
        ok = (
            IDirectoryURI.providedBy(uri) and
            not uri.is_readonly()
        )
        if ok:
            return
        raise TypeError(
            "Upload dirnode was {!r}, must be a read-write directory node.".format(
                value,
            ),
        )

    @inline_callbacks
    def list(self):
        result = yield self._collective_dirnode.list()
        returnValue(list(
            participant_from_dmd(
                name,
                dirobj,
                self._is_self(dirobj),
            )
            for (name, (dirobj, metadata))
            in result.items()
        ))

    def _is_self(self, dirobj):
        return dirobj.get_readonly_uri() == self._upload_dirnode.get_readonly_uri()


@implementer(IParticipant)
@attr.s(frozen=True)
class _CollectiveDirnodeParticipant(object):
    """
    An ``IParticipant`` implementation backed by a Tahoe-LAFS directory node
    (a DMD).

    :ivar unicode name: A human-readable identifier for this participant.  It
        will be the name of the DMD directory in the collective.

    :ivar bytes dircap: The capability-string of the dirnode for this
        participant (aka "upload_dircap").

    :ivar bool is_self: True if this participant is known to represent the
        ourself, False otherwise.  Concretely, "ourself" is whoever can write
        to the directory node.

    :ivar TahoeClient _tahoe_client: Internal use. The Tahoe-LAFS API
        client currently being used.
    """
    name = attr.ib(validator=attr.validators.instance_of(unicode))
    dircap = attr.ib(validator=attr.validators.instance_of(bytes))
    is_self = attr.ib(validator=attr.validators.instance_of(bool))
    _tahoe_client = attr.ib()

    def files(self):
        """
        List the children of the directory node, decode their paths, and return a
        Deferred which fires with a dictionary mapping all of the paths to
        more details.
        """
        d = self._tahoe_client.list_directory(self.dircap)
        d.addCallback(
            lambda listing_map: {
                magic2path(encoded_relpath_u): FolderFile(child, metadata)
                for (encoded_relpath_u, (child, metadata))
                in listing_map.items()
            },
        )
        return d


@attr.s
class FolderFile(object):
    """
    A file associated with some metadata in a particular container (such as a
    Tahoe-LAFS directory node).

    :ivar allmydata.interfaces.IFilesystemNode node: The Tahoe-LAFS node for
        the underlying file content.

    :ivar dict metadata: Metadata associated with the file content in the
        containing directory.
    """
    node = attr.ib()
    metadata = attr.ib()

    @property
    def version(self):
        return self.metadata["version"]
