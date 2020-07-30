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
    IDirectoryNode,
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

    def list():
        """
        Get all of the files this participant has uploaded to this magic folder.
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


def participants_from_collective(collective_dirnode, upload_dirnode):
    """
    Get an ``IParticipants`` provider that reads participants from the given
    Tahoe-LAFS dirnodes.

    :param IDirectoryNode collective_dirnode: The magic folder "collective"
        directory into which participant DMDs are linked.

    :param IDirectoryNode upload_dirnode: The DMD for ourself, used to
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
        ok = (
            IDirectoryNode.providedBy(value) and
            not value.is_unknown() and
            value.is_readonly()
        )
        if ok:
            return
        raise TypeError(
            "Collective dirnode was {!r}, must be a read-only directory node.".format(
                value,
            ),
        )

    @_upload_dirnode.validator
    def mutable_dirnode(self, attribute, value):
        ok = (
            IDirectoryNode.providedBy(value) and
            not value.is_unknown() and
            not value.is_readonly()
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
    name = attr.ib(validator=attr.validators.instance_of(unicode))
    dirobj = attr.ib()
    is_self = attr.ib(validator=attr.validators.instance_of(bool))

    def list(self):
        d = self.dirobj.list()
        d.addCallback(
            lambda listing_map: {
                magic2path(encoded_relpath_u): _FolderFile(child, metadata)
                for (encoded_relpath_u, (child, metadata))
                in listing_map.items()
            },
        )
        return d


@attr.s
class _FolderFile(object):
    node = attr.ib()
    metadata = attr.ib()

    @property
    def version(self):
        return self.metadata["version"]
