import json

from testtools.matchers import (
    Equals,
    Contains,
    ContainsDict,
    MatchesListwise,
    MatchesAll,
    Always,
)

from hyperlink import (
    DecodedURL,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    Clock,
)
from twisted.internet.defer import (
    succeed,
    Deferred,
    inlineCallbacks,
)

from .common import (
    SyncTestCase,
    AsyncTestCase,
)
from ..util.capabilities import (
    Capability,
)
from ..config import (
    create_testing_configuration,
)
from ..testing.web import (
    create_tahoe_treq_client,
    create_fake_tahoe_root,
)
from ..invite import (
    InMemoryInviteManager,
    validate_versions,
    accept_invite,
)
from ..service import (
    MagicFolderService,
)
from ..status import (
    EventsWebSocketStatusService,
)

from magic_folder.snapshot import (
    create_local_author,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
    CannotAddDirectoryEntryError,
    TahoeAPIError,
)
from wormhole._interfaces import (
    IDeferredWormhole,
)
from zope.interface import (
    implementer,
)


@implementer(IDeferredWormhole)
class FakeWormhole:
    """
    A local-only fake of the Wormhole interfaces
    """
    def __init__(self, to_send=None, fake_code=None, message_errors=None):
        self._did_send = []
        self._will_receive = [] if to_send is None else to_send
        self._message_errors = [] if message_errors is None else message_errors
        self._code = fake_code
        self._closed = False
        self._want_codes = []  # List[Deferred]

    def get_sent_messages(self):
        """
        Return one dict representing each message that got sent (in
        order).
        """
        return [
            json.loads(msg)
            for msg in self._did_send
        ]

    def get_welcome(self):
        return succeed(dict())

    def allocate_code(self, code_length=2):
        if not self._code:
            self._code = "1-cranky-woodlark"
        if self._want_codes:
            receivers = self._want_codes
            self._want_codes = []
            for recv in receivers:
                recv.callback(self._code)
        return succeed(self._code)

    def get_code(self):
        d = Deferred()
        if self._message_errors:
            err = self._message_errors.pop(0)
            d.errback(err)
        elif self._code is not None:
            d.callback(self._code)
        else:
            self._want_codes.append(d)
        return d

    def set_code(self, code):
        self._code = code
        # XXX

    def input_code(self):
        raise NotImplementedError()

    def get_unverified_key(self):
        raise NotImplementedError()

    def get_verifier(self):
        raise NotImplementedError()

    def get_versions(self):
        return succeed({
            "magic-folder": {
                "supported-messages": ["invite-v1"]
            }
        })

    def derive_key(self, purpose, length):
        raise NotImplementedError()

    def send_message(self, msg):
        self._did_send.append(msg)

    def get_message(self):
        d = Deferred()
        if self._will_receive:
            msg = self._will_receive.pop(0)
            d.callback(msg)
        else:
            d.errback(RuntimeError("no more messages"))
        return d

    def close(self):
        assert not self._closed, "closed more than once"
        self._closed = True


class TestInviteManager(SyncTestCase):
    """
    Tests for InviteManager
    """

    def setUp(self):
        self.basedir = FilePath(self.mktemp())
        self.nodedir = FilePath(self.mktemp())
        self.basedir.makedirs()

        self.reactor = Clock()

        self.root = create_fake_tahoe_root()
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(self.root),
        )

        class FakeStatus:
            def __init__(self):
                self.errors = []

            def error_occurred(self, err):
                self.errors.append(err)
        self.status = FakeStatus()

        self.global_config = create_testing_configuration(self.basedir, self.nodedir)

        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self.upload_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2:", b"")[1]
        )
        self.invitee_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2:", b"")[1]
        )
        dirdata = [
            "dirnode",
            {
                "children": {
                    u"Margaret Hamilton": [
                        "filenode", {
                            "mutable": False,
                            "ro_uri": self.upload_dircap.to_readonly().danger_real_capability_string(),
                            "format": "SDMF",
                        }
                    ],
                }
            }
        ]
        self.collective_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2-RO:", json.dumps(dirdata).encode("utf8"))[1]
        )
        self.config = self.global_config.create_magic_folder(
            u"foldername",
            self.magic_path,
            create_local_author(u"Margaret Hamilton"),
            self.collective_dircap,
            self.upload_dircap,
            60,
            None,
        )

        self.manager = InMemoryInviteManager(
            self.tahoe_client,
            self.status,
            self.config,
        )
        # we seed the wormhole with all the messages from the other side
        self.wormhole = FakeWormhole([
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
                "personal": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),

            json.dumps({
                "success": True,
            }).encode("utf8"),
        ])

        return super(TestInviteManager, self).setUp()

    def test_crud(self):
        """
        We can create, list, (there's no update), delete (finalize) invites.
        """
        inv = self.manager.create_invite(
            self.reactor,
            "Adele Goldberg",
            "read-write",
            self.wormhole,
        )

        # the invite caused appropriate wormhole messages to be sent
        self.assertThat(
            self.wormhole.get_sent_messages(),
            MatchesListwise([
                ContainsDict({
                    "kind": Equals("join-folder"),
                    "protocol": Equals("invite-v1"),
                    "collective": Equals(self.collective_dircap.danger_real_capability_string()),
                    "participant-name": Equals("Adele Goldberg"),
                    "mode": Equals("read-write"),
                }),
                ContainsDict({
                    "success": Equals(True),
                }),
            ]),
        )

        # we can list that same invite
        self.assertThat(
            self.manager.list_invites(),
            MatchesListwise([
                ContainsDict({
                    "id": Equals(inv.uuid),
                }),
            ])
        )

        # we can get the invite back
        self.assertThat(
            self.manager.get_invite(inv.uuid),
            Equals(inv),
        )

        # we can serialize the invite
        self.assertThat(
            inv.marshal(),
            ContainsDict({
                "id": Always(), # '48c773ff-ad0f-46a9-9ae0-69b2ed5aeb86',
                "participant-name": Equals("Adele Goldberg"),
                "consumed": Equals(True),
                "success": Equals(True),
                "wormhole-code": Equals(None),
            })
        )

    def test_delete_invite(self):
        """
        We can delete an in-progress invite
        """
        self.wormhole = FakeWormhole([])
        inv = self.manager.create_invite(
            self.reactor,
            "Kay McNulty",
            "read-write",
            self.wormhole,
        )

        # we can delete the invite
        self.manager.cancel_invite(inv.uuid)

        # now there is nothing left in the list
        self.assertThat(
            self.manager.list_invites(),
            Equals([])
        )

    def test_error(self):
        """
        An error during wormhole is logged
        """
        # arrange for an error
        self.wormhole._message_errors.append(RuntimeError("bad stuff"))

        inv = self.manager.create_invite(
            self.reactor,
            "Adele Goldberg",
            "read-write",
            self.wormhole,
        )

        # the error was logged
        self.assertThat(
            self.status.errors,
            MatchesListwise([
                MatchesAll(
                    Contains("Adele Goldberg"),
                    Contains("bad stuff"),
                )
            ])
        )

        # serialization makes sense
        self.assertThat(
            inv.marshal(),
            ContainsDict({
                "id": Always(), # '48c773ff-ad0f-46a9-9ae0-69b2ed5aeb86',
                "participant-name": Equals("Adele Goldberg"),
                "consumed": Equals(False),
                "success": Equals(False),
                "wormhole-code": Equals(None),
            })
        )

    def test_explicit_reject(self):
        """
        The 'other side' says no, nicely.
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "join-folder-reject",
                "protocol": "invite-v1",
                "reject-reason": "not feeling it",
            }).encode("utf8"),
        ]

        inv = self.manager.create_invite(
            self.reactor,
            "Mary Keller",
            "read-write",
            self.wormhole,
        )

        # the error was logged
        self.assertThat(
            self.status.errors,
            MatchesListwise([
                MatchesAll(
                    Contains("Mary Keller"),
                    Contains("not feeling it"),
                )
            ])
        )

        # serialization makes sense
        self.assertThat(
            inv.marshal(),
            ContainsDict({
                "id": Always(), # '48c773ff-ad0f-46a9-9ae0-69b2ed5aeb86',
                "participant-name": Equals("Mary Keller"),
                "consumed": Equals(True),
                "success": Equals(False),
                "wormhole-code": Equals(None),
            })
        )

    def test_invalid_version(self):
        """
        Invalid version for the reply message
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "join-folder-reject",
                "protocol": "not-an-invite-protocol",
                "reject-reason": "won't even matter",
            }).encode("utf8"),
        ]

        self.manager.create_invite(
            self.reactor,
            "Jean Bartik",
            "read-write",
            self.wormhole,
        )

        # the error was logged
        self.assertThat(
            self.status.errors,
            MatchesListwise([
                MatchesAll(
                    Contains("Invite of 'Jean Bartik' failed"),
                )
            ])
        )

    def test_invalid_personal_dmd(self):
        """
        The personal DMD sent back is incorrect
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
                # not read-only -- other invalid ones could be immutable, ...?
                "personal": self.invitee_dircap.danger_real_capability_string(),
            }).encode("utf8"),
        ]
        self.manager.create_invite(
            self.reactor,
            "Frances Holder",
            "read-write",
            self.wormhole,
        )

        # the error was logged
        self.assertThat(
            self.status.errors,
            MatchesListwise([
                MatchesAll(
                    Contains("Frances Holder"),
                    Contains("must be a read-only directory"),
                )
            ])
        )


class TestVersionParser(SyncTestCase):
    """
    Tests related to the app-versions validation
    """

    def test_happy(self):
        """
        the only correct versions dict works
        """
        self.assertThat(
            validate_versions({
                "magic-folder": {
                    "supported-messages": ["invite-v1"]
                }
            }),
            Equals(None)
        )

    def test_no_magic_folders(self):
        """
        error if there's no magic-folders key at all
        """
        with self.assertRaises(ValueError):
            validate_versions({})

    def test_no_supported_messages(self):
        """
        error if there's no supported messages
        """
        with self.assertRaises(ValueError):
            validate_versions({
                "magic-folder": {}
            })

    def test_no_invite_v1(self):
        """
        error if there's no "invite-v1" in supported
        """
        with self.assertRaises(ValueError):
            validate_versions({
                "magic-folder": {
                    "supported-messages": [],
                }
            })


class TestService(AsyncTestCase):
    """
    Tests for invite-related service functions
    """

    def setUp(self):
        self.basedir = FilePath(self.mktemp())
        self.nodedir = FilePath(self.mktemp())
        self.basedir.makedirs()

        self.reactor = Clock()

        self.root = create_fake_tahoe_root()
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(self.root),
        )

        class FakeStatus:
            def __init__(self):
                self.errors = []

            def error_occurred(self, err):
                self.errors.append(err)
        self.status = FakeStatus()

        self.global_config = create_testing_configuration(self.basedir, self.nodedir)

        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self.upload_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2:", b"")[1]
        )
        self.invitee_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2:", b"")[1]
        )
        dirdata = [
            "dirnode",
            {
                "children": {
                    u"Margaret Hamilton": [
                        "filenode", {
                            "mutable": False,
                            "ro_uri": self.upload_dircap.to_readonly().danger_real_capability_string(),
                            "format": "SDMF",
                        }
                    ],
                }
            }
        ]
        self.collective_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2-RO:", json.dumps(dirdata).encode("utf8"))[1]
        )
        self.config = self.global_config.create_magic_folder(
            u"foldername",
            self.magic_path,
            create_local_author(u"Margaret Hamilton"),
            self.collective_dircap,
            self.upload_dircap,
            60,
            None,
        )

        self.manager = InMemoryInviteManager(
            self.tahoe_client,
            self.status,
            self.config,
        )
        # we seed the wormhole with all the messages from the other side
        self.wormhole = FakeWormhole([
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
                "personal": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),

            json.dumps({
                "success": True,
            }).encode("utf8"),
        ])

        self.global_status = EventsWebSocketStatusService(
            self.reactor,
            self.global_config,
        )

        self.service = MagicFolderService(
            self.reactor,
            self.global_config,
            self.global_status,
            self.tahoe_client,
        )

        def create_wormhole(*args, **kw):
            return self.wormhole
        self.service._wormhole_factory = create_wormhole

        return super(TestService, self).setUp()

    @inlineCallbacks
    def test_folder_invite(self):
        """
        Create an invite for a particular folder
        """
        self.wormhole = FakeWormhole([
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
                "personal": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),
        ])
        invite = yield self.service.invite_to_folder("foldername", "Elizabeth Feinler", "read-write")
        self.assertThat(
            invite.is_accepted(),
            Equals(True)
        )

    @inlineCallbacks
    def test_error_cannot_edit_mutable(self):
        """
        Tahoe can't create the directory entry
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
                "personal": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),
        ]

        # arrange for some tahoe error to happen
        self.tahoe_client.mutables_bad(
            CannotAddDirectoryEntryError(
                "foo",
                TahoeAPIError(500, "some tahoe error"),
            )
        )
        with self.assertRaises(Exception):
            answer = yield self.service.invite_to_folder(
                "foldername",
                "Kathleen Booth",
                "read-write"
            )
            yield answer.await_done()

    @inlineCallbacks
    def test_folder_invite_duplicate_participant(self):
        """
        Create an invite for a particular folder (but already have that
        participant)
        """
        self.wormhole = FakeWormhole([
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
                "personal": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),
        ])
        yield self.service.invite_to_folder("foldername", "Kay McNulty", "read-write")

        # new wormhole for the new invite

        self.wormhole = FakeWormhole([])
        with self.assertRaises(ValueError):
            yield self.service.invite_to_folder("foldername", "Kay McNulty", "read-write")

    @inlineCallbacks
    def test_folder_invite_capability_mismatch(self):
        """
        Invite someone read-only but they send a capability back
        """
        self.wormhole = FakeWormhole([
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
                "personal": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),
        ])
        with self.assertRaises(ValueError) as ctx:
            invite = yield self.service.invite_to_folder("foldername", "Betty Jennings", "read-only")
            yield invite.await_done()
        self.assertThat(
            str(ctx.exception),
            Equals("Read-only peer sent a Personal capability"),
        )

    @inlineCallbacks
    def test_folder_invite_capability_mismatch_write(self):
        """
        Invite someone read-write and they don't send a capability back
        """
        self.wormhole = FakeWormhole([
            json.dumps({
                "kind": "join-folder-accept",
                "protocol": "invite-v1",
            }).encode("utf8"),
        ])
        with self.assertRaises(ValueError) as ctx:
            invite = yield self.service.invite_to_folder("foldername", "Betty Snyder", "read-write")
            yield invite.await_done()
        self.assertThat(
            str(ctx.exception),
            Contains("did not send a Personal capability")
        )
        self.assertThat(
            invite.is_accepted(),
            Equals(False)
        )


class TestAcceptInvite(AsyncTestCase):
    """
    Tests relating to the accepting side of an invite via
    accept_invite()
    """

    def setUp(self):
        self.basedir = FilePath(self.mktemp())
        self.nodedir = FilePath(self.mktemp())
        self.folder_dir = FilePath(self.mktemp())

        self.basedir.makedirs()
        self.folder_dir.makedirs()

        self.reactor = Clock()

        self.root = create_fake_tahoe_root()
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(self.root),
        )

        class FakeStatus:
            def __init__(self):
                self.errors = []

            def error_occurred(self, err):
                self.errors.append(err)
        self.status = FakeStatus()

        self.global_config = create_testing_configuration(self.basedir, self.nodedir)

        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self.upload_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2:", b"")[1]
        )
        self.invitee_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2:", b"")[1]
        )
        dirdata = [
            "dirnode",
            {
                "children": {
                    u"Margaret Hamilton": [
                        "filenode", {
                            "mutable": False,
                            "ro_uri": self.upload_dircap.to_readonly().danger_real_capability_string(),
                            "format": "SDMF",
                        }
                    ],
                }
            }
        ]
        self.collective_dircap = Capability.from_string(
            self.root.add_mutable_data(u"URI:DIR2-RO:", json.dumps(dirdata).encode("utf8"))[1]
        )
        self.config = self.global_config.create_magic_folder(
            u"foldername",
            self.magic_path,
            create_local_author(u"Margaret Hamilton"),
            self.collective_dircap,
            self.upload_dircap,
            60,
            None,
        )

        self.manager = InMemoryInviteManager(
            self.tahoe_client,
            self.status,
            self.config,
        )
        # we seed the wormhole with all the messages from the other side
        self.wormhole = FakeWormhole([
            json.dumps({
                "kind": "join-folder",
                "protocol": "invite-v1",
                "collective": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),

            json.dumps({
                "success": True,
            }).encode("utf8"),
        ])

        self.global_status = EventsWebSocketStatusService(
            self.reactor,
            self.global_config,
        )

        self.service = MagicFolderService(
            self.reactor,
            self.global_config,
            self.global_status,
            self.tahoe_client,
        )

        def create_wormhole(*args, **kw):
            return self.wormhole
        self.service._wormhole_factory = create_wormhole

        return super(TestAcceptInvite, self).setUp()

    @inlineCallbacks
    def test_accept(self):
        result = yield accept_invite(
            self.reactor,
            self.global_config,
            "1-foo-bar", "manilla", "Marlyn Meltzer",
            self.folder_dir, 30, 30,
            self.tahoe_client, self.wormhole
        )
        self.assertThat(
            result,
            Equals({"success": True})
        )

    @inlineCallbacks
    def test_error_poll_interval(self):
        """
        poll interval must be positive
        """
        with self.assertRaises(ValueError):
            yield accept_invite(
                self.reactor,
                self.global_config,
                "1-foo-bar", "manilla", "Fran Bilas", self.folder_dir,
                -1,
                30,
                self.tahoe_client,
                self.wormhole
            )

    @inlineCallbacks
    def test_error_local_dir_exist(self):
        """
        local_dir must exist
        """
        with self.assertRaises(ValueError) as ctx:
            yield accept_invite(
                self.reactor,
                self.global_config,
                "1-foo-bar", "manilla", "Fran Bilas",
                FilePath("/dev/null"),
                30,
                30,
                self.tahoe_client,
                self.wormhole
            )
        self.assertThat(
            str(ctx.exception),
            Contains("must be an existing directory")
        )

    @inlineCallbacks
    def test_error_writable_cap(self):
        """
        collective must be read-only
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "join-folder",
                "protocol": "invite-v1",
                "collective": self.invitee_dircap.danger_real_capability_string(),
            }).encode("utf8"),
        ]

        with self.assertRaises(ValueError) as ctx:
            yield accept_invite(
                self.reactor,
                self.global_config,
                "1-foo-bar", "manilla", "Gloria Ruth Gordon",
                self.folder_dir, 30, 30,
                self.tahoe_client,
                self.wormhole
            )
        self.assertThat(
            str(ctx.exception),
            Contains("must be read-only")
        )

    @inlineCallbacks
    def test_error_wrong_kind(self):
        """
        wrong sort of first message
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "not-join-folder",
                "protocol": "invite-v1",
                "collective": self.invitee_dircap.danger_real_capability_string(),
            }).encode("utf8"),
        ]

        with self.assertRaises(ValueError) as ctx:
            yield accept_invite(
                self.reactor,
                self.global_config,
                "1-foo-bar", "manilla", "Ruth Lichterman",
                self.folder_dir, 30, 30,
                self.tahoe_client,
                self.wormhole
            )
        self.assertThat(
            str(ctx.exception),
            Contains("Expected a 'join-folder' message")
        )

    @inlineCallbacks
    def test_error_wrong_protocol(self):
        """
        wrong sort of first message
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "join-folder",
                "protocol": "not-invite-v1",
                "collective": self.invitee_dircap.danger_real_capability_string(),
            }).encode("utf8"),
        ]

        with self.assertRaises(ValueError) as ctx:
            yield accept_invite(
                self.reactor,
                self.global_config,
                "1-foo-bar", "manilla", "Kathleen Booth",
                self.folder_dir, 30, 30,
                self.tahoe_client,
                self.wormhole
            )
        self.assertThat(
            str(ctx.exception),
            Contains("Invalid protocol")
        )

    @inlineCallbacks
    def test_error_no_collective(self):
        """
        must have a collective
        """
        self.wormhole._will_receive = [
            json.dumps({
                "kind": "join-folder",
                "protocol": "invite-v1",
            }).encode("utf8"),
        ]

        with self.assertRaises(ValueError) as ctx:
            yield accept_invite(
                self.reactor,
                self.global_config,
                "1-foo-bar", "manilla", "Kathleen Booth",
                self.folder_dir, 30, 30,
                self.tahoe_client,
                self.wormhole
            )
        self.assertThat(
            str(ctx.exception),
            Contains("No 'collective'")
        )
