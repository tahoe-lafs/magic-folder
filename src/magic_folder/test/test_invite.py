import io
import json
from tempfile import mktemp

from testtools.matchers import (
    Equals,
    Contains,
    ContainsDict,
    MatchesListwise,
    MatchesStructure,
    MatchesAll,
    AfterPreprocessing,
    Always,
    HasLength,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)
from testtools import (
    ExpectedException,
)

from hypothesis import (
    assume,
    given,
    note,
)
from hypothesis.strategies import (
    binary,
    text,
    just,
    one_of,
    integers,
)

from hyperlink import (
    DecodedURL,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    Cooperator,
    Clock,
)
from twisted.internet.defer import (
    succeed,
    Deferred,
)

from .common import (
    SyncTestCase,
    AsyncTestCase,
    success_result_of,
)
from .strategies import (
    magic_folder_filenames,
    remote_authors,
    author_names,
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
)
from ..service import (
    MagicFolderService,
)
from ..status import (
    WebSocketStatusService,
)

from magic_folder.snapshot import (
    create_local_author,
    create_author_from_json,
    create_author,
    create_snapshot,
    create_snapshot_from_capability,
    write_snapshot_to_tahoe,
    LocalSnapshot,
    UnknownPropertyError,
    MissingPropertyError,
    format_filenode,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
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
            recievers = self._want_codes
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
        raise NotImplementedError()

    def input_code(self):
        raise NotImplementedError()

    def get_unverified_key(self):
        raise NotImplementedError()

    def get_verifier(self):
        raise NotImplementedError()

    def get_versions(self):
        raise NotImplementedError()

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
                "magic-folder-invite-version": 1,
                "personal-dmd": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),

            json.dumps({
                "success": True,
            }).encode("utf8"),
        ])

        return super(TestInviteManager, self).setUp()

    def test_crud(self):
        """
        We can create, list, (there's no update), delete invites.
        """
        inv = self.manager.create_invite(
            self.reactor,
            "Adele Goldberg",
            self.wormhole,
        )

        # the invite caused appropriate wormhole messages to be sent
        self.assertThat(
            self.wormhole.get_sent_messages(),
            MatchesListwise([
                ContainsDict({
                    "magic-folder-invite-version": Equals(1),
                    "collective-dmd": Equals(self.collective_dircap.danger_real_capability_string()),
                    "petname": Equals("Adele Goldberg"),
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
                "petname": Equals("Adele Goldberg"),
                "consumed": Equals(True),
                "success": Equals(True),
                "wormhole-code": Equals(None),
            })
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
                "petname": Equals("Adele Goldberg"),
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
                "magic-folder-invite-version": 1,
                "reject-reason": "not feeling it",
            }).encode("utf8"),
        ]

        inv = self.manager.create_invite(
            self.reactor,
            "Mary Keller",
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
                "petname": Equals("Mary Keller"),
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
                "magic-folder-invite-version": 0,
                "reject-reason": "won't even matter",
            }).encode("utf8"),
        ]

        inv = self.manager.create_invite(
            self.reactor,
            "Jean Bartik",
            self.wormhole,
        )

        # the error was logged
        self.assertThat(
            self.status.errors,
            MatchesListwise([
                MatchesAll(
                    Contains("Invalid invite reply version"),
                )
            ])
        )

    def test_invalid_personal_dmd(self):
        """
        The personal DMD sent back is incorrect
        """
        self.wormhole._will_receive = [
            json.dumps({
                "magic-folder-invite-version": 1,
                # not read-only -- other invalid ones could be immutable, ...?
                "personal-dmd": self.invitee_dircap.danger_real_capability_string(),
            }).encode("utf8"),
        ]
        inv = self.manager.create_invite(
            self.reactor,
            "Frances Holder",
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
                "magic-folder-invite-version": 1,
                "personal-dmd": self.invitee_dircap.to_readonly().danger_real_capability_string(),
            }).encode("utf8"),

            json.dumps({
                "success": True,
            }).encode("utf8"),
        ])

        self.global_status = WebSocketStatusService(
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
            print("ding", args, kw)
            return FakeWormhole()
        self.service._wormhole_factory=create_wormhole

        return super(TestService, self).setUp()

    def test_folder_invite(self):
        """
        Create an invite for a particular folder
        """
        d = self.service.invite_to_folder("foldername", "Elizabeth Feinler")
        print(id(self.service._wormhole_factory))
        print(id(self.manager._invites))
        return d
