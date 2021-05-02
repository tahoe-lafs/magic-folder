# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder invite command and supporting code.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)

from zope.interface import (
    Interface,
    Attribute,
    implementer,
)

import wormhole


def magic_folder_invite(config, folder_name, suggested_invitee_name, treq):
    """
    Invite a user identified by the nickname to a folder owned by the alias

    :param GlobalConfigDatabase config: our configuration

    :param unicode folder_name: The name of an existing magic-folder

    :param unicode suggested_invitee_name: petname for the invited device

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred[unicode]: A secret magic-wormhole invitation code.
    """
    # FIXME TODO
    # see https://github.com/LeastAuthority/magic-folder/issues/232
    raise NotImplementedError



class IInviteCollection(Interface):
    """
    A collection of Invites
    """

    def list_invites(self):
        """
        An iterable of IInvite instances
        """

    def create_invite(self, suggested_petname, ):
        """
        Create a brand new IInvite and add it to our collection

        :returns: the IInvite
        """


class IInvite(Interface):
    """
    A particular Invite
    """

    # folder is a MagicFolder instance I guess?
    folder = Attribute("The folder this Invite pertains to")
    suggested_petname = Attribute("Our preferred petname for this participant")
    wormhole_code = Attribute("The wormhole code for this invite (or None)")


@implementer(IInvite)
@attr.s
class Invite(object):
    """
    An invite.

    Create new invites using IInviteManager.create
    """
    uuid = attr.ib()  # unique ID
    suggested_petname = attr.ib()
    _collection = attr.ib()  # IInviteCollection instance
    _wormhole = attr.ib()  # wormhole.IWormhole() instance
    # I guess actually wormhole.IDeferredWormhole ..

    @property
    def wormhole_code(self):
        """
        Our wormhole code. ``None`` if we haven't allocated it yet.
        """
        return None

    def marshal(self):
        """
        :returns: JSON-able dict representing this Invite
        """
        return {
            "id": self.uuid,
            "suggested-petname": self.suggested_petname,
            # None on the invitee side, str on inviter side
            "wormhole-code": None,
        }


@implementer(IInviteCollection)
@attr.s
class InMemoryInviteManager(object):
    """
    A manager of Invites that keeps all state in memory (only).
    """

    folder_service = attr.ib()  # MagicFolderService instnace
    _invites = attr.ib()  # dict: uuid -> Invite

    def list_invites(self):
        return [
            invite.marshal()
            for invite in self._invites.values()
        ]

    def create_invite(self, reactor, suggested_petname):
        wormhole = wormhole.create(
            appid="tahoe-lafs.org/magic-folder/invite",
            relay_url=wormhole.cli.public_relay.RENDEZVOUS_RELAY,
            reactor=reactor,
        )
        invite = Invite(
            suggested_petname,
            self,
            wormhole,
        )
