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
    inlineCallbacks,
    returnValue,
)

from eliot.twisted import (
    inline_callbacks,
)

from allmydata.interfaces import (
    IReadonlyDirectoryURI,
    IDirectoryURI,
)
from allmydata.uri import (
    from_string as tahoe_uri_from_string,
)

from .magicpath import (
    magic2path,
)
from .snapshot import (
    RemoteAuthor,
)
from .tahoe_client import (
    CannotAddDirectoryEntryError,
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

    @inlineCallbacks
    def add(author, personal_dmd_cap):
        """
        Add a new participant to this collective.

        :param IRemoteAuthor author: personal details of new participant

        :param bytes personal_dmd_cap: the read-capability of the new
            participant (if it is a write-capability then it's "us"
            but by definition we already have an "us" participant in
            any existing Magic Folder and so that's an error here).

        :returns IParticipant: the new participant
        """


def participant_from_dmd(name, dirnode, is_self, tahoe_client):
    """
    Create an ``IParticipant`` provider backed by the DMD at the given
    location.

    :param unicode name: The nickname of this participant.

    :param bytes dirnode: Capability to the Tahoe-LAFS directory node
        that holds this participant's state.

    :param bool is_self: ``True`` if we know this participant represents
        ourself in the magic folder, ``False`` otherwise.

    :param TahoeClient tahoe_client: Access to Tahoe-LAFS API.

    :return IParticipant: A participant object for accessing this
        participant's state.
    """
    return _CollectiveDirnodeParticipant(name, dirnode, is_self, tahoe_client)


def participants_from_collective(collective_dirnode, upload_dirnode, tahoe_client):
    """
    Get an ``IParticipants`` provider that reads participants from the given
    Tahoe-LAFS dirnodes.

    :param IDirectoryNode collective_dirnode: The magic folder "collective"
        directory into which participant DMDs are linked.

    :param IDirectoryNode upload_dirnode: The DMD for ourself, used to
        identify which participant is ourself.

    :param TahoeClient tahoe_client: Access to Tahoe-LAFS API.

    :return: An ``IParticipants`` provider.
    """
    return _CollectiveDirnodeParticipants(collective_dirnode, upload_dirnode, tahoe_client)


@implementer(IParticipants)
@attr.s(frozen=True)
class _CollectiveDirnodeParticipants(object):
    _collective_cap = attr.ib()
    _upload_cap = attr.ib()
    _tahoe_client = attr.ib(hash=None)

    @_collective_cap.validator
    def any_dirnode(self, attribute, value):
        """
        The Collective DMD must be a directory capability (but could be a
        read-only one or a read-write one).
        """
        uri = tahoe_uri_from_string(value)
        if IReadonlyDirectoryURI.providedBy(uri) or IDirectoryURI.providedBy(uri):
            return
        raise TypeError(
            "Collective dirnode was {!r}, must be a directory node.".format(
                value,
            ),
        )

    @_upload_cap.validator
    def mutable_dirnode(self, attribute, value):
        """
        The Upload DMD must be a writable directory capability
        """
        uri = tahoe_uri_from_string(value)
        if IDirectoryURI.providedBy(uri):
            if not uri.is_readonly():
                return
        raise TypeError(
            "Upload dirnode was {!r}, must be a read-write directory node.".format(
                value,
            ),
        )

    @inlineCallbacks
    def add(self, author, personal_dmd_cap):
        """
        IParticipants API
        """
        uri = tahoe_uri_from_string(personal_dmd_cap)
        if not IReadonlyDirectoryURI.providedBy(uri):
            raise ValueError(
                "New participant Personal DMD must be read-only dircap"
            )
        if not isinstance(author, RemoteAuthor):
            raise ValueError(
                "Author must be a RemoteAuthor instance"
            )
        # semantically, it doesn't make sense to allow a second
        # participant with the very same Personal DMD as another (even
        # if the name/author is different). So, we check here .. but
        # there is a window for race between the check and when we add
        # the participant. The only real solution here would be a
        # magic-folder-wide write-lock or to serialize all Tahoe
        # operations (at least across one magic-folder).
        participants = yield self.list()
        if any(personal_dmd_cap == p.dircap for p in participants):
            raise ValueError(
                "Already have a participant with Personal DMD '{}'".format(personal_dmd_cap)
            )

        # NB: we could check here if there is already a participant
        # for this name .. however, there's a race between that check
        # succeeding and adding the participant so we just try to add
        # and let Tahoe send us an error by using "replace=False"
        try:
            yield self._tahoe_client.add_entry_to_mutable_directory(
                self._collective_cap,
                author.name,
                personal_dmd_cap.encode("ascii"),
                replace=False,
            )
        except CannotAddDirectoryEntryError:
            raise ValueError(
                u"Already have a participant called '{}'".format(author.name)
            )

    @inlineCallbacks
    def list(self):
        """
        IParticipants API
        """
        result = yield self._tahoe_client.list_directory(self._collective_cap)
        returnValue(list(
            participant_from_dmd(
                name,
                dirobj,
                self._is_self(dirobj),
                self._tahoe_client,
            )
            for (name, (dirobj, metadata))
            in result.items()
        ))

    def _is_self(self, dirobj):
        return tahoe_uri_from_string(dirobj).get_readonly().to_string() == tahoe_uri_from_string(self._upload_cap).get_readonly().to_string()


@implementer(IParticipant)
@attr.s(frozen=True)
class _CollectiveDirnodeParticipant(object):
    """
    An ``IParticipant`` implementation backed by a Tahoe-LAFS directory node
    (a DMD).

    :ivar unicode name: A human-readable identifier for this participant.  It
        will be the name of the DMD directory in the collective.

    :ivar bytes dircap: Directory-capability (read or read-write)
        containing this participant's files.

    :ivar bool is_self: True if this participant is known to represent the
        ourself, False otherwise.  Concretely, "ourself" is whoever can write
        to the directory node.
    """
    name = attr.ib(validator=attr.validators.instance_of(unicode))
    dircap = attr.ib(validator=attr.validators.instance_of(bytes))
    is_self = attr.ib(validator=attr.validators.instance_of(bool))
    _tahoe_client = attr.ib()

    @inline_callbacks
    def files(self):
        """
        List the children of the directory node, decode their paths, and return a
        Deferred which fires with a dictionary mapping all of the paths to
        more details.
        """
        result = yield self._tahoe_client.list_directory(self.dircap)
        returnValue({
            magic2path(encoded_relpath_u): FolderFile(child, metadata)
            for (encoded_relpath_u, (child, metadata))
            in result.items()
        })


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
