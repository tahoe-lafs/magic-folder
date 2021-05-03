# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder invite command and supporting code.
"""

import json
from uuid import (
    uuid4,
)
from zope.interface import (
    Interface,
    Attribute,
    implementer,
)
from twisted.application import (
    service,
)
from twisted.internet.defer import (
    inlineCallbacks,
)
from allmydata.interfaces import (
    IDirnodeURI,
)
from allmydata.uri import (
    from_string as tahoe_uri_from_string,
)

import attr
import wormhole
from wormhole.cli.public_relay import (
    RENDEZVOUS_RELAY,
)

from .snapshot import (
    create_local_author,
)


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


@attr.s
class InviteRejected(Exception):
    """
    The other side has (nicely) rejected our invite with a provided
    reason.
    """
    invite=attr.ib()
    reason=attr.ib()


@attr.s
class InvalidInviteReply(Exception):
    """
    Something is semantically invalid about an invite reply
    """
    invite=attr.ib()
    reason=attr.ib()

    def __str__(self):
        return "InvateInviteReply({})".format(self.reason)


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
    _code = None
    _petname = None
    _accepted = None

    @inlineCallbacks
    def start(self, reactor, mf_config, tahoe_client):
        """
        Performs the invite.

        :param IReactor reactor:

        :param MagicFolderConfig mf_config: the Magic Folder this
            invite is part of.

        :param TahoeClient tahoe_client: make Tahoe-LAFS API calls
        """
        collective = tahoe_uri_from_string(mf_config.collective_dircap)
        collective_readcap = collective.get_readonly()
        print(collective)
        print(collective_readcap)

        welcome = yield self._wormhole.get_welcome()
        print("got welcome {}".format(welcome))
        self._wormhole.allocate_code(2)
        self._code = yield self._wormhole.get_code()
        print("code: {}".format(self._code))

        invite_message = json.dumps({
            "magic-folder-invite-version": 1,
            "collective-dmd": collective_readcap.to_string(),
            "suggested-petname": self.suggested_petname,
        }).encode("utf8")
        self._wormhole.send_message(invite_message)

        reply_data = yield self._wormhole.get_message()
        reply_msg = json.loads(reply_data.decode("utf8"))
        print("reply: {}".format(reply_msg))

        version = reply_msg.get("magic-folder-invite-version", None)
        self._code = None  # done with code, it's consumed

        try:
            if version != 1:
                raise ValueError(
                    "Invalid invite reply version: {}".format(version)
                )
            if "reject-reason" in reply_msg:
                self._accepted = False
                raise InviteRejected(
                    invite=self,
                    reason=reply_msg["reject-reason"],
                )

            self._accepted = True
            self._petname = self.suggested_petname
            if "preferred-petname" in reply_msg:
                # max 40 chars, no newlines
                sanitized_petname = reply_msg["preferred-petname"][:40].replace("\n", " ")
                self._petname = sanitized_petname
            personal_dmd = tahoe_uri_from_string(reply_msg["personal-dmd"])
            if not IDirnodeURI.providedBy(personal_dmd):
                raise InvalidInviteReply(
                    invite=self,
                    reason="Personal DMD must be a directory",
                )
            if not personal_dmd.is_readonly():
                raise InvalidInviteReply(
                    invite=self,
                    reason="Personal DMD must be readonly",
                )

            # everything checks out; add the invitee to our Collective DMD
            yield tahoe_client.add_entry_to_mutable_directory(
                mutable_cap=mf_config.collective_dircap,
                path_name=self._petname,
                entry_cap=personal_dmd.to_string(),
            )

        finally:
            # whether due to errors above or happy-path, we are done
            # with the wormhole
            yield self._wormhole.close()

    @property
    def petname(self):
        """
        Return our actual petname thus far
        """
        if self._petname is None:
            return self.suggested_petname
        return self._petname

    @property
    def wormhole_code(self):
        """
        Our wormhole code. ``None`` if we haven't allocated it yet.
        """
        return self._code

    def is_accepted(self):
        """
        :returns: True if we've communcated to the other side and hear
            back that they accept otherwise False
        """
        if self._accepted:
            return True
        return False

    def marshal(self):
        """
        :returns: JSON-able dict representing this Invite
        """
        return {
            "id": self.uuid,
            "petname": self.petname,
            "accepted": True if self.is_accepted() else False,
            # None on the invitee side, str on inviter side
            "wormhole-code": self.wormhole_code,
        }


@inlineCallbacks
def accept_invite(reactor, global_config, wormhole_code, folder_name, author_name, local_dir, poll_interval, tahoe_client):
    """
    This does the opposite side of the invite to Invite.start()
    above. That is:

    - create a fresh Personal DMD
    - extract the read-capability to the Personal DMD
    - send back our preferred petname and read-cap
    - (await seeing our name added to the Collective DMD?)
    """
    wh = wormhole.create(
        appid=u"tahoe-lafs.org/magic-folder/invite",
        relay_url=RENDEZVOUS_RELAY,
        reactor=reactor,
    )
    welcome = yield wh.get_welcome()
    print("got welcome {}".format(welcome))
    wh.set_code(wormhole_code)
    invite_data = yield wh.get_message()
    invite_msg = json.loads(invite_data.decode("utf8"))
    print("invite: {}".format(invite_msg))

    version = invite_msg.get("magic-folder-invite-version", None)
    try:
        if version != 1:
            raise ValueError(
                "Invalid invite version: {}".format(version)
            )

        # extract the Collective DMD
        if "collective-dmd" not in invite_msg:
            raise ValueError(
                "No 'collective-dmd' in invite"
            )
        collective_dmd = invite_msg["collective-dmd"]
        if not tahoe_uri_from_string(collective_dmd).is_readonly():
            raise ValueError(
                "The 'collective-dmd' must be read-only"
            )

        # what to do with 'suggested-petname'?

        # create a new Personal DMD for our new magic-folder
        personal_dmd = yield tahoe_client.create_mutable_directory()
        personal_cap = tahoe_uri_from_string(personal_dmd)
        personal_readonly_cap = personal_cap.get_readonly()

        # create our author
        author = create_local_author(author_name)

        # create our "state" directory for this magic-folder
        state_dir = global_config.get_default_state_path(folder_name)
        print("CREATE", folder_name, local_dir, state_dir, author, collective_dmd, personal_cap.to_string(), poll_interval)
        global_config.create_magic_folder(
            folder_name,
            local_dir,
            state_dir,
            author,
            str(collective_dmd),
            personal_cap.to_string(),
            poll_interval,
        )

        # send back our invite-reply
        reply = {
            "magic-folder-invite-version": 1,
            "personal-dmd": personal_readonly_cap.to_string(),
            "preferred-petname": author_name,
        }
        yield wh.send_message(json.dumps(reply).encode("utf8"))

    finally:
        # whether due to errors above or happy-path, we are done
        # with the wormhole
        yield wh.close()


@implementer(IInviteCollection)
@implementer(service.IService)
@attr.s
class InMemoryInviteManager(service.Service):
    """
    A manager of Invites that keeps all state in memory (only).
    """

    # XXX maybe better to have one of these per folder, so we can
    # remember mf_config in class

    # XXX maybe better to pass tahoe_client in to this

    tahoe_client = attr.ib()  # magic_folder.tahoe_client.TahoeClient
    # "parent" from service.Service will be our MagicFolderService instance
    _invites = attr.ib(default=attr.Factory(dict))  # dict: uuid -> Invite
    _in_progress = attr.ib(default=attr.Factory(list))  # list[Deferred]

    def list_invites(self):
        """
        :returns: iterable of JSON-able dict's describing all Invites
        """
        return [
            invite.marshal()
            for invite in self._invites.values()
        ]

    def create_invite(self, reactor, suggested_petname, mf_config):
        """
        Create a fresh invite and add it to ourselves.

        :param IReactor reactor:

        :param str suggested_petname: None or a user-defined petname
            for the invited participant.
        """
        wh = wormhole.create(
            appid=u"tahoe-lafs.org/magic-folder/invite",
            relay_url=RENDEZVOUS_RELAY,
            reactor=reactor,
        )
        invite = Invite(
            uuid=str(uuid4()),
            suggested_petname=suggested_petname,
            collection=self,
            wormhole=wh,
        )
        self._invites[invite.uuid] = invite

        d = invite.start(reactor, mf_config, self.tahoe_client)
        d.addCallback(self._invite_succeeded, d, invite)
        d.addErrback(self._invite_failed, d, invite)
        self._in_progress.append(d)

        return invite

    def stopService(self):
        for d in self._in_progress:
            d.cancel()

    def _invite_succeeded(self, d, invite, value):
        try:
            self._in_progress.remove(d)
        except ValueError:
            pass
        # XXX log this, somehow. Probably want to "emit an event" too
        # (e.g. for GridSync)
        print("Invite succeeded: {}".format(invite))

    def _invite_failed(self, d, invite, fail):
        try:
            self._in_progress.remove(d)
        except ValueError:
            pass
        # XXX log this, somehow. Probably want to "emit an event" too
        # (e.g. for GridSync)
        print("Invite failed: {}: {}".format(invite, fail))
