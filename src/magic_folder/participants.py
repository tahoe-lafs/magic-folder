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

from eliot.twisted import (
    inline_callbacks,
)

from .magicpath import (
    magic2path,
    path2magic,
)
from .snapshot import (
    RemoteAuthor,
)
from .tahoe_client import (
    CannotAddDirectoryEntryError,
)
from .util.capabilities import (
    Capability,
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

        :return Deferred[dict[unicode, SnapshotEntry]]: All of the contents
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

    @inline_callbacks
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

class IWriteableParticipant(Interface):
    """
    An ``IWriteableParticipant`` provider represents a participant
    in a particular magic-folder that we have write-access to.
    """

    @inline_callbacks
    def update_snapshot(relpath, capability):
        """
        Update the snapshot with the given relpath.
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
    writer = attr.ib(
        init=False,
        default=attr.Factory(
            lambda self: _WriteableParticipant(self._upload_cap, self._tahoe_client),
            takes_self=True,
        ),
    )

    @_collective_cap.validator
    def _any_dirnode(self, attribute, value):
        """
        The Collective DMD must be a directory capability (but could be a
        read-only one or a read-write one).
        """
        if not isinstance(value, Capability):
            raise TypeError(
                "Collective dirnode was {} not Capability".format(type(value))
            )
        if value.is_directory():
            return
        raise TypeError(
            "Collective dirnode was {!r}, must be a directory Capability.".format(
                value,
            ),
        )

    @_upload_cap.validator
    def _mutable_dirnode(self, attribute, value):
        """
        The Upload DMD must be a writable directory capability
        """
        if not isinstance(value, Capability):
            raise TypeError(
                "Upload dirnode was {} not Capability".format(type(value))
            )
        if value.is_mutable_directory():
            return
        raise TypeError(
            "Upload dirnode was {!r}, must be a read-write directory Capability.".format(
                value,
            ),
        )

    @inline_callbacks
    def add(self, author, personal_dmd_cap):
        """
        IParticipants API
        """
        if not isinstance(personal_dmd_cap, Capability):
            raise TypeError(
                "New participant Personal DMD was {} not Capability".format(
                    type(personal_dmd_cap).__name__,
                )
            )
        if not personal_dmd_cap.is_readonly_directory():
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
                personal_dmd_cap,
                replace=False,
            )
        except CannotAddDirectoryEntryError:
            raise ValueError(
                u"Already have a participant called '{}'".format(author.name)
            )

    @inline_callbacks
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
            if name not in ["@metadata"]
        ))

    def _is_self(self, dirobj):
        return dirobj.to_readonly() == self._upload_cap.to_readonly()


@implementer(IParticipant)
@attr.s(frozen=True)
class _CollectiveDirnodeParticipant(object):
    """
    An ``IParticipant`` implementation backed by a Tahoe-LAFS directory node
    (a DMD).

    :ivar unicode name: A human-readable identifier for this participant.  It
        will be the name of the DMD directory in the collective.

    :ivar Capability dircap: Directory-capability (read or read-write)
        containing this participant's files.

    :ivar bool is_self: True if this participant is known to represent the
        ourself, False otherwise.  Concretely, "ourself" is whoever can write
        to the directory node.
    """
    name = attr.ib(validator=attr.validators.instance_of(str))
    dircap = attr.ib(validator=attr.validators.instance_of(Capability))
    is_self = attr.ib(validator=attr.validators.instance_of(bool))
    _tahoe_client = attr.ib(hash=False)

    @inline_callbacks
    def files(self):
        """
        List the snapshots of this participant, decode their paths, and return a
        Deferred which fires with a dictionary mapping all of the paths to
        more details.
        """
        result = yield self._tahoe_client.list_directory(self.dircap)
        returnValue({
            magic2path(mangled_relpath): SnapshotEntry(child, metadata)
            for (mangled_relpath, (child, metadata))
            in result.items()
            if mangled_relpath not in ["@metadata"]
        })


@implementer(IWriteableParticipant)
@attr.s(frozen=True, order=False)
class _WriteableParticipant(object):
    """
    An ``IWriteableParticipant`` implementation backed by a Tahoe-LAFS directory node
    (a DMD).

    :ivar Capability upload_cap: Read-write directory-capability containing this
        participant's files.
    """
    upload_cap = attr.ib()
    _tahoe_client = attr.ib(eq=False, hash=False)

    @upload_cap.validator
    def _mutable_dirnode(self, attribute, value):
        """
        The Upload DMD must be a writable directory capability
        """
        if not isinstance(value, Capability):
            raise TypeError(
                "Upload dirnode was {} not Capability".format(type(value))
            )
        if value.is_mutable_directory():
            return
        raise TypeError(
            "Upload dirnode was {!r}, must be a read-write directory Capability.".format(
                value,
            ),
        )

    def update_snapshot(self, relpath, capability):
        """
        Update the snapshot with the given relpath.
        """
        return self._tahoe_client.add_entry_to_mutable_directory(
            self.upload_cap,
            path2magic(relpath),
            capability,
            replace=True,
        )



@attr.s
class SnapshotEntry(object):
    """
    A file associated with some metadata in a particular container (such as a
    Tahoe-LAFS directory node).

    :ivar Capability snapshot_cap: The capability for the underlying snapshot

    :ivar dict metadata: Metadata associated with the file content in the
        containing directory.
    """
    snapshot_cap = attr.ib(validator=attr.validators.instance_of(Capability))
    metadata = attr.ib()

    @property
    def version(self):
        return self.metadata["version"]
