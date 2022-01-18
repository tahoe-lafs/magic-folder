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
    Deferred,
    returnValue,
    succeed,
)
from .util.capabilities import (
    to_readonly_capability,
)

import attr
import wormhole
from wormhole.cli.public_relay import (
    RENDEZVOUS_RELAY,
)
from eliot import (
    start_action,
)
from eliot.twisted import (
    inline_callbacks,
)

from .participants import (
    participants_from_collective,
)
from .snapshot import (
    create_local_author,
)
from .tahoe_client import (
    CannotAddDirectoryEntryError,
)
from .util.capabilities import (
    is_readonly_directory_cap,
    to_readonly_capability,
)


def magic_folder_invite(options):
    """
    Invite a user identified by the nickname to a folder
    """
    client = options.parent.client
    return client.invite(
        options["folder"],
        options.petname,
    )


def magic_folder_invite_wait(options, invite_id):
    """
    Await the wormhole completion for a given invite
    """
    client = options.parent.client
    return client.invite_wait(
        options["folder"],
        invite_id,
    )


class IInviteCollection(Interface):
    """
    A collection of Invites
    """

    def list_invites(self):
        """
        An iterable of IInvite instances
        """

    def get_invite(self, id_):
        """
        :returns IInvite: an invite with the given ID (or KeyError)
        """

    def create_invite(self, petname):
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
    petname = Attribute("Our petname for this participant")
    wormhole_code = Attribute("The wormhole code for this invite (or None)")


@attr.s
class InviteError(Exception):
    """
    Base class for all invite-related errors
    """
    invite = attr.ib()
    reason = attr.ib()

    def to_json(self):
        return {
            "invite": self.invite,
            "reason": self.reason,
        }


@attr.s
class InviteRejected(InviteError):
    """
    The other side has (nicely) rejected our invite with a provided
    reason.
    """


@attr.s
class InvalidInviteReply(InviteError):
    """
    Something is semantically invalid about an invite reply
    """

    def __str__(self):
        return "InvalidInviteReply({})".format(self.reason)


@implementer(IInvite)
@attr.s
class Invite(object):
    """
    An invite.

    Create new invites using IInviteManager.create
    """
    uuid = attr.ib()  # unique ID
    petname = attr.ib()
    _collection = attr.ib()  # IInviteCollection instance
    _wormhole = attr.ib()  # wormhole.IWormhole() instance
    # I guess actually wormhole.IDeferredWormhole ..
    _code = None
    _consumed = None  # True if the wormhole code was consumed
    _success = None  # True if succeeded, False if something went wrong
    _reject_reason = None  # if _success is False, this will say why
    _awaiting_code = attr.ib(default=attr.Factory(list))
    _awaiting_done = attr.ib(default=attr.Factory(list))

    def await_code(self):
        """
        :returns Deferred[None]: fires when we have the invite code
        """
        if self._code is not None:
            return succeed(None)
        d = Deferred()
        self._awaiting_code.append(d)
        return d

    def await_done(self):
        """
        :returns Deferred[None]: fires when the wormhole is completed
            (this could mean an error or that the other side accepted the
            invite).
        """
        if self._consumed and self._success is not None:
            return succeed(None)
        d = Deferred()
        self._awaiting_done.append(d)
        return d

    @inline_callbacks
    def perform_invite(self, reactor, mf_config, tahoe_client):
        """
        Performs the invite.

        :param IReactor reactor:

        :param MagicFolderConfig mf_config: the Magic Folder this
            invite is part of.

        :param TahoeClient tahoe_client: make Tahoe-LAFS API calls
        """
        action = start_action(
            action_type="invite:start",
            folder=mf_config.name,
        )
        with action:
            participants = participants_from_collective(
                mf_config.collective_dircap,
                mf_config.upload_dircap,
                tahoe_client,
            )
            existing_devices = yield participants.list()
            collective_readcap = to_readonly_capability(mf_config.collective_dircap)

            if self.petname in (dev.name for dev in existing_devices):
                raise ValueError(
                    "Already have participant '{}'".format(self.petname)
                )

            with start_action(action_type="invite:welcome"):
                welcome = yield self._wormhole.get_welcome()
                if 'motd' in welcome:
                    print(welcome['motd'])

            with start_action(action_type="invite:get_code") as action_code:
                self._wormhole.allocate_code(2)
                self._code = yield self._wormhole.get_code()
                action_code.add_success_fields(code=self._code)
                for d in self._awaiting_code:
                    d.callback(None)

            with start_action(action_type="invite:send_message") as action_msg:
                invite_message = json.dumps({
                    "magic-folder-invite-version": 1,
                    "collective-dmd": collective_readcap,
                    "petname": self.petname,
                }).encode("utf8")
                self._wormhole.send_message(invite_message)

            with start_action(action_type="invite:get_reply") as action_reply:
                reply_data = yield self._wormhole.get_message()
                reply_msg = json.loads(reply_data.decode("utf8"))

            version = reply_msg.get("magic-folder-invite-version", None)
            self._code = None  # done with code, it's consumed
            self._consumed = True

            try:
                if version != 1:
                    raise ValueError(
                        "Invalid invite reply version: {}".format(version)
                    )
                if "reject-reason" in reply_msg:
                    self._success = False
                    self._reject_reason = reply_msg["reject-reason"]
                    raise InviteRejected(
                        invite=self,
                        reason=reply_msg["reject-reason"],
                    )

                personal_dmd = reply_msg["personal-dmd"]
                if not is_readonly_directory_cap(personal_dmd):
                    raise InvalidInviteReply(
                        invite=self,
                        reason="Personal DMD must be a read-only directory",
                    )

                # everything checks out; add the invitee to our Collective DMD
                try:
                    yield tahoe_client.add_entry_to_mutable_directory(
                        mutable_cap=mf_config.collective_dircap,
                        path_name=self.petname,
                        entry_cap=personal_dmd,
                    )
                except CannotAddDirectoryEntryError as e:
                    self._success = False
                    self._reject_reason = "Failed to add '{}' to collective: {}".format(
                        self.petname,
                        e,
                    )
                    final_message = {
                        "success": False,
                        "error": self._reject_reason,
                    }
                else:
                    final_message = {
                        "success": True,
                        "petname": self.petname,
                    }
                    self._success = True

                yield self._wormhole.send_message(
                    json.dumps(final_message).encode("utf8")
                )

            finally:
                # whether due to errors above or happy-path, we are done
                # with the wormhole
                yield self._wormhole.close()

                for d in self._awaiting_done:
                    d.callback(None)

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
        if self._consumed and self._success:
            return True
        return False

    def marshal(self):
        """
        :returns: JSON-able dict representing this Invite
        """
        return {
            "id": self.uuid,
            "petname": self.petname,
            "consumed": True if self._consumed else False,
            "success": True if self._success else False,
            # None on the invitee side, str on inviter side
            "wormhole-code": self.wormhole_code,
        }


@inline_callbacks
def accept_invite(reactor, global_config, wormhole_code, folder_name, author_name, local_dir, poll_interval, scan_interval, tahoe_client):
    """
    This does the opposite side of the invite to Invite.perform_invite()
    above. That is:

    - create a fresh Personal DMD
    - extract the read-capability to the Personal DMD
    - send back our preferred petname and read-cap
    - (await seeing our name added to the Collective DMD?)

    :param unicode wormhole_code:
    :param unicode folder_name:
    :param unicode author_name:
    :param FilePath local_dir:
    :param int poll_interval:
    :param int scan_interval:
    """
    if poll_interval < 1:
        raise ValueError(
            "'poll-interval' must be a positive integer"
        )
    if not local_dir.exists() and local_dir.isdir():
        raise ValueError(
            "Local path '{}' must be an existing directory".format(
                local_dir.path,
            )
        )

    wh = wormhole.create(
        appid=u"tahoe-lafs.org/magic-folder/invite",
        relay_url=RENDEZVOUS_RELAY,
        reactor=reactor,
    )
    welcome = yield wh.get_welcome()
    if 'motd' in welcome:
        print(welcome['motd'])
    wh.set_code(wormhole_code)
    with start_action(action_type="join:get_invite") as action_code:
        invite_data = yield wh.get_message()
        invite_msg = json.loads(invite_data.decode("utf8"))
        action_code.add_success_fields(invite=invite_msg)

    version = invite_msg.get("magic-folder-invite-version", None)
    try:
        if version != 1:
            raise ValueError(
                "Invalid invite-msg version: {}".format(version)
            )

        # extract the Collective DMD
        if "collective-dmd" not in invite_msg:
            raise ValueError(
                "No 'collective-dmd' in invite"
            )
        collective_dmd = invite_msg["collective-dmd"]
        if not is_readonly_directory_cap(collective_dmd):
            raise ValueError(
                "The 'collective-dmd' must be read-only"
            )

        # create a new Personal DMD for our new magic-folder
        with start_action(action_type="join:create_personal_dmd") as action_dmd:
            personal_dmd = yield tahoe_client.create_mutable_directory()
            personal_readonly_cap = to_readonly_capability(personal_dmd)

        # create our author
        author = create_local_author(author_name)

        # now create the actual magic-folder locally
        with start_action(action_type="join:create_folder") as action_folder:
            action_folder.add_success_fields(
                name=folder_name,
                path=local_dir.path,
            )
            try:
                global_config.create_magic_folder(
                    folder_name,
                    local_dir,
                    author,
                    str(collective_dmd),
                    personal_dmd,  # need read-write capability here
                    poll_interval,
                    scan_interval,
                )
            except Exception as e:
                reply = {
                    "magic-folder-invite-version": 1,
                    "reject-reason": "Failed to create folder locally"
                }
                yield wh.send_message(json.dumps(reply).encode("utf8"))
                raise

        # send back our invite-reply
        reply = {
            "magic-folder-invite-version": 1,
            "personal-dmd": personal_readonly_cap,
        }
        yield wh.send_message(json.dumps(reply).encode("utf8"))

        # final ack
        final = yield wh.get_message()
        final_msg = json.loads(final.decode("utf8"))
        if not final_msg["success"]:
            raise RuntimeError(
                "Accepting invite failed: {}".format(final_msg["error"])
            )
        returnValue(final_msg)

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
    # remember mf_config in class (we do, so we can ..)

    tahoe_client = attr.ib()  # magic_folder.tahoe_client.TahoeClient
    folder_status = attr.ib()  # magic_folder.status.FolderStatus
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

    def get_invite(self, id_):
        """
        :returns IInvite: an invite with the given ID (or KeyError)
        """
        return self._invites[id_]

    def create_invite(self, reactor, petname, mf_config):
        """
        Create a fresh invite and add it to ourselves.

        :param IReactor reactor:

        :param str petname: None or a user-defined petname
            for the invited participant.
        """
        wh = wormhole.create(
            appid=u"tahoe-lafs.org/magic-folder/invite",
            relay_url=RENDEZVOUS_RELAY,
            reactor=reactor,
        )
        invite = Invite(
            uuid=str(uuid4()),
            petname=petname,
            collection=self,
            wormhole=wh,
        )
        self._invites[invite.uuid] = invite

        d = invite.perform_invite(reactor, mf_config, self.tahoe_client)
        d.addCallback(self._invite_succeeded, d, invite)
        d.addErrback(self._invite_failed, d, invite)
        self._in_progress.append(d)

        return invite

    def stopService(self):
        for d in self._in_progress:
            d.cancel()

    def _invite_succeeded(self, invite, d, value):
        try:
            self._in_progress.remove(d)
        except ValueError:
            pass
        # XXX log this, somehow. Probably want to "emit an event" too
        # (e.g. for GridSync)
        print("Invite succeeded: {}".format(value))
        return value

    def _invite_failed(self, fail, d, invite):
        try:
            self._in_progress.remove(d)
        except ValueError:
            pass
        self.folder_status.error_occurred(
            "Invite of '{}' failed: {}".format(
                invite.petname,
                invite._reject_reason,
            )
        )
        for x in invite._awaiting_code:
            x.errback(fail)
        for x in invite._awaiting_done:
            x.errback(fail)
        return
