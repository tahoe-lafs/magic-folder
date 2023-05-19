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
from twisted.python.failure import (
    Failure,
)

import attr
from eliot import (
    start_action,
)
from eliot.twisted import (
    inline_callbacks,
)
from wormhole.errors import (
    WormholeError,
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
    Capability,
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

    def create_invite(self, reactor, participant_name, mode, wormhole):
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
    participant_name = Attribute("Our name for this participant")
    participant_mode = Attribute("read-only or read-write")
    wormhole_code = Attribute("The wormhole code for this invite (or None)")


@attr.s(auto_exc=True)
class InviteError(Exception):
    """
    Base class for all invite-related errors
    """
    invite = attr.ib()
    reason = attr.ib()


@attr.s(auto_exc=True)
class InviteRejected(InviteError):
    """
    The other side has (nicely) rejected our invite with a provided
    reason.
    """


@attr.s(auto_exc=True)
class InvalidInviteReply(InviteError):
    """
    Something is semantically invalid about an invite reply
    """


@implementer(IInvite)
@attr.s
class Invite(object):
    """
    An invite.

    Create new invites using IInviteManager.create
    """
    uuid = attr.ib()  # unique ID
    participant_name = attr.ib()
    participant_mode = attr.ib(validator=attr.validators.in_(["read-only", "read-write"]))
    _collection = attr.ib()  # IInviteCollection instance
    _wormhole = attr.ib()  # wormhole.IDeferredWormhole instance
    _code = None  # if non-None, our wormhole code
    _consumed = None  # True if the wormhole code was consumed
    _success = None  # True if succeeded, False if something went wrong
    _reject_reason = None  # if _success is False, this will say why
    _awaiting_code = attr.ib(default=attr.Factory(list))
    _awaiting_done = attr.ib(default=attr.Factory(list))
    _had_error = attr.ib(default=None)  # if this invite ever failed, this is the failure
    _perform_d = attr.ib(default=None)  # Deferred instance if we're in perform_invite()
    _cancelled = attr.ib(default=None)

    def await_code(self):
        """
        :returns Deferred[None]: fires when we have the invite code
        """
        if self._code is not None:
            return succeed(None)
        if self._had_error is not None:
            return self._had_error
        d = Deferred()
        self._awaiting_code.append(d)
        return d

    def await_done(self):
        """
        :returns Deferred[None]: fires when the wormhole is completed
            (this could mean an error or that the other side accepted the
            invite).
        """
        if self._consumed and self._success:
            return succeed(None)
        if self._had_error is not None:
            return self._had_error
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
            collective_readcap = mf_config.collective_dircap.to_readonly()

            if self.participant_name in (dev.name for dev in existing_devices):
                raise ValueError(
                    "Already have participant '{}'".format(self.participant_name)
                )

            with start_action(action_type="invite:welcome"):
                welcome = yield self._wormhole.get_welcome()
                if 'motd' in welcome:
                    print(welcome['motd'])

            with start_action(action_type="invite:get_code") as action_code:
                self._wormhole.allocate_code(2)
                self._code = yield self._wormhole.get_code()
                action_code.add_success_fields(code=self._code)
                while self._awaiting_code:
                    d = self._awaiting_code.pop()
                    d.callback(None)

            versions = yield self._wormhole.get_versions()
            validate_versions(versions)

            with start_action(action_type="invite:send_message"):
                invite_message = json.dumps({
                    "kind": "join-folder",
                    "protocol": "invite-v1",
                    "folder-name": mf_config.name,
                    "collective": collective_readcap.danger_real_capability_string(),
                    "participant-name": self.participant_name,
                    "mode": self.participant_mode,
                }).encode("utf8")
                self._wormhole.send_message(invite_message)

            with start_action(action_type="invite:get_reply"):
                reply_data = yield self._wormhole.get_message()
                reply_msg = json.loads(reply_data.decode("utf8"))

            # we are done with the code, but keep it in self._code
            # because (especially in testing) it's possible to have
            # run this whole method -- because all results are
            # synchronously available -- before .await_code() gets
            # called.
            self._consumed = True

            try:
                if reply_msg.get("protocol", None) != "invite-v1":
                    raise ValueError(
                        "Invalid invite reply protocol"
                    )
                kind = reply_msg.get("kind", None)
                if kind == "join-folder-reject":
                    self._success = False
                    self._reject_reason = reply_msg.get("reject-reason", "No reason given")
                    raise InviteRejected(
                        invite=self,
                        reason=self._reject_reason,
                    )

                elif kind == "join-folder-accept":
                    if "personal" in reply_msg:
                        if self.participant_mode == "read-only":
                            raise ValueError(
                                "Read-only peer sent a Personal capability"
                            )
                        personal_dmd = Capability.from_string(reply_msg["personal"])
                        if not personal_dmd.is_readonly_directory():
                            raise InvalidInviteReply(
                                invite=self,
                                reason="Personal DMD must be a read-only directory",
                            )
                    else:
                        if self.participant_mode != "read-only":
                            raise ValueError(
                                "Peer with mode '{}' did not send a Personal capability".format(
                                    self.participant_mode,
                                )
                            )
                        # to mark a read-only participant, we use an
                        # empty immutable directory
                        personal_dmd = yield tahoe_client.create_immutable_directory({})

                else:  # unhandled "kind"
                    raise ValueError(
                        "Invalid reply kind '{}'".format(kind)
                    )


                # everything checks out; add the invitee to our Collective DMD
                try:
                    yield tahoe_client.add_entry_to_mutable_directory(
                        mutable_cap=mf_config.collective_dircap,
                        path_name=self.participant_name,
                        entry_cap=personal_dmd,
                    )
                except CannotAddDirectoryEntryError as e:
                    self._success = False
                    self._had_error = Failure()
                    self._reject_reason = "Failed to add '{}' to collective: {}".format(
                        self.participant_name,
                        e,
                    )
                    final_message = {
                        "kind": "join-folder-ack",
                        "protocol": "invite-v1",
                        "error": self._reject_reason,
                        "success": False,
                    }
                except Exception:
                    self._had_error = Failure()
                    raise
                else:
                    self._success = True
                    final_message = {
                        "kind": "join-folder-ack",
                        "protocol": "invite-v1",
                        "participant-name": self.participant_name,
                        "success": True,
                    }

                yield self._wormhole.send_message(
                    json.dumps(final_message).encode("utf8")
                )

            finally:
                # whether due to errors above or happy-path, we are done
                # with the wormhole
                yield self._wormhole.close()

                while self._awaiting_done:
                    d = self._awaiting_done.pop(0)
                    d.callback(None)

    @property
    def wormhole_code(self):
        """
        Our wormhole code. ``None`` if we haven't allocated it yet.
        """
        return self._code if not self._consumed else None

    def is_accepted(self):
        """
        :returns: True if we've communcated to the other side and hear
            back that they accept otherwise False
        """
        if self._consumed and self._success:
            return True
        return False

    def is_consumed(self):
        """
        :returns: True if this invite has been consumed (successfully or
            not)
        """
        if self._consumed:
            return True
        return False

    def marshal(self):
        """
        :returns: JSON-able dict representing this Invite
        """
        return {
            "id": self.uuid,
            "participant-name": self.participant_name,
            "consumed": True if self._consumed else False,
            "success": True if self._success else False,
            # None on the invitee side, str on inviter side
            "wormhole-code": self.wormhole_code,
        }


def validate_versions(versions):
    """
    Confirm that the app-versions message contains semantically valid and correct information from the peer

    :raises ValueError: upon any problems
    """
    mf = versions.get('magic-folder', None)
    if not mf:
        raise ValueError(
            "Other side doesn't support 'magic-folder' protocol"
        )
    supported_messages = mf.get('supported-messages', [])
    if "invite-v1" not in supported_messages:
        raise ValueError(
            "Other side doesn't support 'invite-v1' messages"
        )


@inline_callbacks
def accept_invite(reactor, global_config, wormhole_code, folder_name, author_name, local_dir, poll_interval, scan_interval, tahoe_client, wh):
    """
    This does the opposite side of the invite to Invite.perform_invite()
    above. That is:

    - create a fresh Personal directory
    - extract the read-capability to the Personal directory
    - send back our read-cap (unless read-only)
    - await final ack message

    :param str wormhole_code: an unused Magic Wormhole code

    :param str folder_name: arbitrary identifier for the new folder

    :param str author_name: arbitrary identifier for ourselves

    :param FilePath local_dir: the local Magic Folder

    :param int poll_interval: seconds between searching for remote updates

    :param int scan_interval: seconds between searching for local updates

    :param IDeferredWormhole wh: the Magic Wormhole object to use
    """
    if poll_interval < 1:
        raise ValueError(
            "'poll-interval' must be a positive integer"
        )
    if not local_dir.exists() or not local_dir.isdir():
        raise ValueError(
            "Local path '{}' must be an existing directory".format(
                local_dir.path,
            )
        )

    welcome = yield wh.get_welcome()
    if 'motd' in welcome:
        # XXX almost certainly shouldn't print() in a "utility" method, but
        # also should surface the MotD somewhere..
        print(welcome['motd'])
    wh.set_code(wormhole_code)

    versions = yield wh.get_versions()
    validate_versions(versions)

    with start_action(action_type="join:get_invite") as action_code:
        invite_data = yield wh.get_message()
        invite_msg = json.loads(invite_data.decode("utf8"))
        action_code.add_success_fields(invite=list(invite_msg.keys()))

    try:
        if invite_msg.get("kind", None) != "join-folder":
            raise ValueError(
                "Expected a 'join-folder' message"
            )
        if invite_msg.get("protocol", None) != "invite-v1":
            raise ValueError(
                "Invalid protocol"
            )

        # extract the Collective DMD
        if "collective" not in invite_msg:
            raise ValueError(
                "No 'collective' in invite"
            )
        collective_dmd = Capability.from_string(invite_msg["collective"])
        if not collective_dmd.is_readonly_directory():
            raise ValueError(
                "The 'collective' must be read-only"
            )

        # create a new Personal DMD for our new magic-folder
        with start_action(action_type="join:create_personal_dmd"):
            personal_dmd = yield tahoe_client.create_mutable_directory()
            personal_readonly_cap = personal_dmd.to_readonly()

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
                    collective_dmd,
                    personal_dmd,  # need read-write capability here
                    poll_interval,
                    scan_interval,
                )
            except Exception:
                reply = {
                    "kind": "join-folder-reject",
                    "protocol": "invite-v1",
                    "reject-reason": "Failed to create folder locally"
                }
                yield wh.send_message(json.dumps(reply).encode("utf8"))
                raise

        # send back our invite-reply
        reply = {
            "kind": "join-folder-accept",
            "protocol": "invite-v1",
            "personal": personal_readonly_cap.danger_real_capability_string(),
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
    tahoe_client = attr.ib()  # magic_folder.tahoe_client.TahoeClient
    folder_status = attr.ib()  # magic_folder.status.FolderStatus
    folder_config = attr.ib()  # magic_folder.config.MagicFolderConfig
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

    def create_invite(self, reactor, participant_name, mode, wh):
        """
        Create a fresh invite and add it to ourselves.

        :param IReactor reactor:

        :param str participant_name: A user-defined name for the
            invited participant.

        :param str mode: read-only or read-write

        :param IDeferredWormhole wh: the Magic Wormhole object to use
        """
        invite = Invite(
            uuid=str(uuid4()),
            participant_name=participant_name,
            participant_mode=mode,
            collection=self,
            wormhole=wh,
        )
        self._invites[invite.uuid] = invite

        d = invite.perform_invite(reactor, self.folder_config, self.tahoe_client)
        d.addCallback(self._invite_succeeded, d, invite)
        d.addErrback(self._invite_failed, d, invite)
        invite._perform_d = d
        self._in_progress.append(d)

        return invite

    @inline_callbacks
    def cancel_invite(self, invite_id):
        """
        Cancel an invite (and delete it), closing the associated wormhole.
        """
        try:
            invite = self._invites[invite_id]
            invite._cancelled = True
        except KeyError:
            raise ValueError(
                "Invite '{}' doesn't exist".format(invite_id)
            )
        if invite.is_consumed():
            raise ValueError(
                "Invite '{}' cannot be canceled".format(invite_id)
            )
        try:
            yield invite._wormhole.close()
        except WormholeError:
            pass

        if invite._perform_d:
            invite._perform_d.cancel()
        del self._invites[invite_id]

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
        # if we meant to cancel this, we don't want to report any
        # error (e.g. we'll probably have a LonelyError from the
        # wormhole, as it closed w/o a partner)
        if not invite._cancelled:
            self.folder_status.error_occurred(
                "Invite of '{}' failed: {}".format(
                    invite.participant_name,
                    invite._reject_reason if invite._reject_reason is not None else str(fail.value),
                )
            )
        invite._had_error = fail
        for x in invite._awaiting_code:
            x.errback(fail)
        for x in invite._awaiting_done:
            x.errback(fail)
        return
