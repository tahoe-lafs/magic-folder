from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Tests relating generally to magic_folder.downloader
"""

import io
import base64
from mock import Mock

from json import (
    dumps,
)

from hyperlink import (
    DecodedURL,
)

from eliot.twisted import (
    inline_callbacks,
)

from testtools.matchers import (
    MatchesStructure,
    Always,
    Equals,
    ContainsDict,
    AfterPreprocessing,
)
from testtools.twistedsupport import (
    succeeded,
    failed,
)
from hypothesis import (
    given,
)
from twisted.internet import reactor
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.defer import (
    inlineCallbacks,
)
from twisted.python.filepath import (
    FilePath,
)
from treq.testing import (
    StubTreq,
    StringStubbingResource,
)

from ..config import (
    create_testing_configuration,
)
from ..downloader import (
    RemoteSnapshotCacheService,
    MagicFolderUpdater,
    InMemoryMagicFolderFilesystem,
)
from ..magic_folder import (
    MagicFolder,
)
from ..snapshot import (
    create_local_author,
    RemoteSnapshot,
    sign_snapshot,
    format_filenode,
    create_snapshot,
    write_snapshot_to_tahoe,
)
from ..status import (
    WebSocketStatusService,
)
from ..tahoe_client import (
    create_tahoe_client,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)

from .common import (
    SyncTestCase,
    AsyncTestCase,
)
from .strategies import (
    tahoe_lafs_immutable_dir_capabilities,
)


class CacheTests(SyncTestCase):
    """
    Tests for ``RemoteSnapshotCacheService``
    """
    def setup_example(self):
        self.author = create_local_author("alice")
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self._global_config = create_testing_configuration(
            FilePath(self.mktemp()),
            FilePath("dummy"),
        )
        self.collective_cap = "URI:DIR2:mfqwcylbmfqwcylbmfqwcylbme:mfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqq"
        self.personal_cap = "URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra"

        self.config = self._global_config.create_magic_folder(
            "default",
            self.magic_path,
            create_local_author("iris"),
            self.collective_cap,
            self.personal_cap,
            1,
            0,
        )
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text("http://invalid./"),
            self.http_client,
        )
        self.cache = RemoteSnapshotCacheService.from_config(
            self.config,
            self.tahoe_client,
        )
        self.cache.startService()

    @given(
        tahoe_lafs_immutable_dir_capabilities(),
    )
    def test_cache_non_exist(self, remote_cap):
        """
        Trying to cache a non-existent capability produces an error
        """
        self.assertThat(
            self.cache.get_snapshot_from_capability(remote_cap),
            failed(Always())
        )

    def test_cache_single(self):
        """
        Caching a single RemoteSnapshot with no parents works
        """
        self.setup_example()
        mangled_name = "foo"
        metadata = {
            "snapshot_version": 1,
            "name": mangled_name,
            "author": self.author.to_remote_author().to_json(),
            "parents": [],
        }
        _, content_cap = self.root.add_data("URI:CHK:", b"content\n" * 1000)
        _, metadata_cap = self.root.add_data("URI:CHK:", dumps(metadata))

        data = dumps([
            "dirnode",
            {
                "mutable": False,
                "children": {
                    "content": [
                        "filenode",
                        {
                            "format": "CHK",
                            "ro_uri": content_cap,
                            "mutable": False,
                            "metadata": {},
                            "size": 4704
                        }
                    ],
                    "metadata": [
                        "filenode",
                        {
                            "format": "CHK",
                            "ro_uri": metadata_cap,
                            "mutable": False,
                            "metadata": {
                                "magic_folder": {
                                    "author_signature": base64.b64encode(
                                        sign_snapshot(
                                            self.author,
                                            mangled_name,
                                            content_cap,
                                            metadata_cap
                                        ).signature
                                    ),
                                }
                            },
                            "size": 246
                        }
                    ]
                }
            }
        ])
        _, cap = self.root.add_data("URI:DIR2-CHK:", data)

        # we've set up some fake data; lets see if the cache service
        # will cache this successfully.
        self.assertThat(
            self.cache.get_snapshot_from_capability(cap),
            succeeded(
                MatchesStructure(
                    name=Equals("foo"),
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": Equals([]),
                        "name": Equals("foo"),
                    }),
                )
            )
        )

    def test_cache_parents(self):
        """
        Caching a RemoteSnapshot with parents works
        """
        self.setup_example()
        mangled_name = "foo"
        parents = []
        genesis = None

        for who in range(5):
            metadata = {
                "snapshot_version": 1,
                "name": mangled_name,
                "author": self.author.to_remote_author().to_json(),
                "parents": parents,
            }
            content_data = u"content {}\n".format(who).encode("utf8") * 1000
            content_cap = self.root.add_data("URI:CHK:", content_data)[1]
            metadata_cap = self.root.add_data("URI:CHK:", dumps(metadata))[1]

            _, cap = self.root.add_data(
                "URI:DIR2-CHK:",
                dumps([
                    "dirnode",
                    {
                        "mutable": False,
                        "children": {
                            "content": [
                                "filenode",
                                {
                                    "format": "CHK",
                                    "ro_uri": content_cap,
                                    "mutable": False,
                                    "metadata": {},
                                    "size": 4704
                                }
                            ],
                            "metadata": [
                                "filenode",
                                {
                                    "format": "CHK",
                                    "ro_uri": metadata_cap,
                                    "mutable": False,
                                    "metadata": {
                                        "magic_folder": {
                                            "author_signature": base64.b64encode(
                                                sign_snapshot(
                                                    self.author,
                                                    mangled_name,
                                                    content_cap,
                                                    metadata_cap
                                                ).signature
                                            ),
                                        }
                                    },
                                    "size": 246
                                }
                            ]
                        }
                    }
                ])
            )
            parents = [cap]
            if genesis is None:
                genesis = cap

        # cache the oldest parent first
        self.assertThat(
            self.cache.get_snapshot_from_capability(genesis),
            succeeded(
                MatchesStructure(
                    name=Equals("foo"),
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": Equals([]),
                        "name": Equals("foo"),
                    }),
                )
            )
        )
        # we should have a single snapshot cached
        self.assertThat(
            len(self.cache._cached_snapshots),
            Equals(1),
        )

        # we've set up some fake data; lets see if the cache service
        # will cache this successfully.
        self.assertThat(
            self.cache.get_snapshot_from_capability(cap),
            succeeded(
                MatchesStructure(
                    name=Equals("foo"),
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": AfterPreprocessing(len, Equals(1)),
                        "name": Equals("foo"),
                    }),
                )
            )
        )
        # we should have cached all the snapshots now
        self.assertThat(
            len(self.cache._cached_snapshots),
            Equals(5),
        )

        # our tahoe client should not be asked anything if we re-cache
        # the already-cached tree of Snapshots .. turn our client into
        # a Mock to absorb any calls it receives.
        self.cache.tahoe_client = Mock()
        self.assertThat(
            self.cache.get_snapshot_from_capability(cap),
            succeeded(
                MatchesStructure(
                    name=Equals("foo"),
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": AfterPreprocessing(len, Equals(1)),
                        "name": Equals("foo"),
                    }),
                )
            )
        )
        self.cache.tahoe_client.assert_not_called()


class UpdateTests(AsyncTestCase):
    """
    Tests for ``MagicFolderUpdater``

    Each test here starts with a shared setup and a single Magic
    Folder called "default":

    - "alice" is us (self.personal_cap)
    - "zara" is another participant (self.other_personal_cap)
    - both are in the Collective
    """

    def setUp(self):
        super(UpdateTests, self).setUp()
        self.author = create_local_author("alice")
        self.other = create_local_author("zara")
        self.magic_path = FilePath(self.mktemp())
        self.magic_path.makedirs()
        self.state_path = FilePath(self.mktemp())
        self.state_path.makedirs()

        self._global_config = create_testing_configuration(
            self.state_path,
            FilePath("dummy"),
        )

        self.root = create_fake_tahoe_root()

        # create the two Personal DMDs in Tahoe
        _, self.personal_cap = self.root.add_data(
            "URI:DIR2:",
            dumps([
                "dirnode",
                {
                    "children": {}
                },
            ]).encode("utf8"),
        )
        _, self.other_personal_cap = self.root.add_data(
            "URI:DIR2:",
            dumps([
                "dirnode",
                {
                    "children": {},
                },
            ]).encode("utf8"),
        )

        # create the Collective DMD with both alice and zara
        _, self.collective_cap = self.root.add_data(
            "URI:DIR2:",
            dumps([
                "dirnode",
                {
                    "children": {
                        self.author.name: format_filenode(self.personal_cap),
                        self.other.name: format_filenode(self.other_personal_cap),
                    }
                },
            ]).encode("utf8"),
        )

        # create our configuration and a magic-folder using the
        # Collective above and alice's Personal DMD
        self.config = self._global_config.create_magic_folder(
            "default",
            self.magic_path,
            self.author,
            self.collective_cap,
            self.personal_cap,
            1,
            0,
        )
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text("http://invalid./"),
            self.http_client,
        )
        self.status_service = WebSocketStatusService()
        self.service = MagicFolder.from_config(
            reactor,
            self.tahoe_client,
            "default",
            self._global_config,
            self.status_service,
        )
        self.service.startService()

    def tearDown(self):
        super(UpdateTests, self).tearDown()
        return self.service.stopService()

    @inlineCallbacks
    def test_create(self):
        """
        Create a RemoteSnapshot and add it to zara's Personal DMD. The
        downloader should fetch it into alice's magic-folder
        """

        content = b"foo" * 1000
        local_snap = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content),
            self.state_path,
        )

        remote_snap = yield write_snapshot_to_tahoe(local_snap, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap.capability.encode("utf8"),
        )

        # wait for the downloader to put this into Alice's magic-folder
        for _ in range(10):
            if "foo" in self.magic_path.listdir():
                if self.magic_path.child("foo").getContent() == content:
                    break
            yield deferLater(reactor, 1.0, lambda: None)
        assert self.magic_path.child("foo").exists()
        assert self.magic_path.child("foo").getContent() == content, "content mismatch"

    @inlineCallbacks
    def test_conflict(self):
        """
        Create a RemoteSnapshot and add it to zara's Personal DMD. The
        downloader should fetch it into alice's magic-folder .. but we
        arrange to have a local file there, so it should be a
        conflict.
        """

        # first put a file "in the way"
        original_content = b"not the right stuff\n"
        self.magic_path.child("foo").setContent(original_content)

        # build up content for zara's Personal DMD
        content = b"foo" * 1000
        local_snap = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content),
            self.state_path,
        )

        remote_snap = yield write_snapshot_to_tahoe(local_snap, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap.capability.encode("utf8"),
        )

        # wait for the downloader to put this into Alice's magic-folder
        for _ in range(10):
            if "foo.conflict-zara" in self.magic_path.listdir():
                break
            yield deferLater(reactor, 1.0, lambda: None)

        # we should conflict
        assert self.magic_path.child("foo.conflict-zara").getContent() == content
        assert self.magic_path.child("foo").getContent() == original_content

    @inlineCallbacks
    def test_update(self):
        """
        Create a snapshot in zara's Personal DMD, then update it.
        """

        content0 = b"foo" * 1000
        content1 = b"bar" * 1000
        local_snap0 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content0),
            self.state_path,
        )

        remote_snap0 = yield write_snapshot_to_tahoe(local_snap0, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap0.capability.encode("utf8"),
        )

        # create an update
        local_snap1 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content1),
            self.state_path,
            parents=[local_snap0],
        )
        remote_snap1 = yield write_snapshot_to_tahoe(local_snap1, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap1.capability.encode("utf8"),
            replace=True,
        )

        # wait for the downloader to put this into Alice's
        # magic-folder, which should (eventually) match the update.
        for _ in range(10):
            if "foo" in self.magic_path.listdir():
                if self.magic_path.child("foo").getContent() == content1:
                    break
            yield deferLater(reactor, 1.0, lambda: None)
        assert self.magic_path.child("foo").exists()
        assert self.magic_path.child("foo").getContent() == content1, "content mismatch"

    @inlineCallbacks
    def test_multi_update(self):
        """
        Create a snapshot in zara's Personal DMD, then update it 2x
        """

        content0 = b"foo" * 1000
        content1 = b"bar" * 1000
        content2 = b"baz" * 1000
        local_snap0 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content0),
            self.state_path,
        )

        remote_snap0 = yield write_snapshot_to_tahoe(local_snap0, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap0.capability.encode("utf8"),
        )

        # create an update
        local_snap1 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content1),
            self.state_path,
            parents=[local_snap0],
        )
        remote_snap1 = yield write_snapshot_to_tahoe(local_snap1, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap1.capability.encode("utf8"),
            replace=True,
        )

        # wait for the downloader to put this into Alice's
        # magic-folder, which should (eventually) match the update.
        for _ in range(10):
            if "foo" in self.magic_path.listdir():
                if self.magic_path.child("foo").getContent() == content1:
                    break
            yield deferLater(reactor, 1.0, lambda: None)
        assert self.magic_path.child("foo").exists()
        assert self.magic_path.child("foo").getContent() == content1, "content mismatch"

        # create a second update
        local_snap2 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content2),
            self.state_path,
            parents=[remote_snap1],
        )
        remote_snap2 = yield write_snapshot_to_tahoe(local_snap2, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap2.capability.encode("utf8"),
            replace=True,
        )

        # wait for the downloader to put this into Alice's
        # magic-folder, which should (eventually) match the update.
        for _ in range(10):
            if "foo" in self.magic_path.listdir():
                if self.magic_path.child("foo").getContent() == content2:
                    break
            yield deferLater(reactor, 1.0, lambda: None)
        assert self.magic_path.child("foo").exists()
        assert self.magic_path.child("foo").getContent() == content2, "content mismatch"


class ConflictTests(AsyncTestCase):
    """
    Tests for ``MagicFolderUpdater``
    """

    def setUp(self):
        super(ConflictTests, self).setUp()
        self.alice = create_local_author("alice")
        self.carol = create_local_author("carol")

        self.alice_magic_path = FilePath(self.mktemp())
        self.alice_magic_path.makedirs()
        self.state_path = FilePath(self.mktemp())
        self.state_path.makedirs()

        self.filesystem = InMemoryMagicFolderFilesystem()

        self._global_config = create_testing_configuration(
            self.state_path,
            FilePath("dummy"),
        )

        self.alice_collective = "URI:DIR2:"
        self.alice_personal = "URI:DIR2:"

        self.alice_config = self._global_config.create_magic_folder(
            "default",
            self.alice_magic_path,
            self.alice,
            self.alice_collective,
            self.alice_personal,
            1,
            0,
        )

        # note, we don't "run" this service, just populate ._cached_snapshots
        self.remote_cache = RemoteSnapshotCacheService(
            folder_config=self.alice_config,
            tahoe_client=None,
        )

        self.tahoe_calls = []

        def get_resource_for(method, url, params, headers, data):
            self.tahoe_calls.append((method, url, params, headers, data))
            return (200, {}, b"{}")

        self.updater = MagicFolderUpdater(
            magic_fs=self.filesystem,
            config=self.alice_config,
            remote_cache=self.remote_cache,
            tahoe_client=create_tahoe_client(
                DecodedURL.from_text(u"http://invalid./"),
                StubTreq(StringStubbingResource(get_resource_for)),
            )
        )

    @inline_callbacks
    def test_update_with_local(self):
        """
        Give the updater a remote update while we also have a LocalSnapshot
        """

        cap0 = b"URI:DIR2-CHK:aaaaaaaaaaaaaaaaaaaaaaaaaa:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:1:5:376"
        remote0 = RemoteSnapshot(
            name="foo",
            author=self.carol,
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[cap0] = remote0

        local0_content = b"dummy content"
        local0 = yield create_snapshot(
            name="foo",
            author=self.alice,
            data_producer=io.BytesIO(local0_content),
            snapshot_stash_dir=self.state_path,
            parents=None,
            raw_remote_parents=None,
        )
        # if we have a local, we must have the path locally
        self.alice_magic_path.child("foo").setContent(local0_content)
        self.alice_config.store_local_snapshot(local0)

        # tell the updater to examine the remote-snapshot
        yield self.updater.add_remote_snapshot(remote0)

        # we have a local-snapshot for the same name as the incoming
        # remote, so this is a conflict

        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", remote0),
                ("conflict", remote0),
            ])
        )

    @inline_callbacks
    def test_update_with_parent(self):
        """
        Give the updater a remote update that has a parent which is 'our'
        snapshot
        """

        parent_cap = b"URI:DIR2-CHK:bbbbbbbbbbbbbbbbbbbbbbbbbb:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:1:5:376"
        parent = RemoteSnapshot(
            name="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        parent_content = b"parent" * 1000
        self.remote_cache._cached_snapshots[parent_cap] = parent
        self.alice_config.store_remotesnapshot("foo", parent)

        cap0 = b"URI:DIR2-CHK:aaaaaaaaaaaaaaaaaaaaaaaaaa:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:1:5:376"
        remote0 = RemoteSnapshot(
            name="foo",
            author=self.carol,
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[parent_cap],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[cap0] = remote0

        # we've 'seen' this file before so we must have the path locally
        self.alice_magic_path.child("foo").setContent(parent_content)

        # tell the updater to examine the remote-snapshot
        yield self.updater.add_remote_snapshot(remote0)

        # we have a local-snapshot for the same name as the incoming
        # remote, so this is a conflict

        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", remote0),
                ("overwrite", remote0),
            ])
        )

    @inline_callbacks
    def test_update_with_old_ancestor(self):
        """
        Give the updater a remote update where 'our' snapshot is the
        grandparent
        """

        remotes = []

        for letter in 'abcd':
            parent_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format(letter * 26, letter * 52)
            parent = RemoteSnapshot(
                name="foo",
                author=self.alice,
                metadata={"modification_time": 0},
                capability=parent_cap,
                parents_raw=[] if not remotes else [remotes[-1].capability],
                content_cap=b"URI:CHK:",
            )
            self.remote_cache._cached_snapshots[parent_cap] = parent
            remotes.append(parent)

        # set "our" parent to the oldest one
        self.alice_config.store_remotesnapshot("foo", remotes[0])

        # we've 'seen' this file before so we must have the path locally
        self.alice_magic_path.child("foo").setContent(b"dummy")

        # tell the updater to examine the youngest remote
        youngest = remotes[-1]
        yield self.updater.add_remote_snapshot(youngest)

        # we have a common ancestor so this should be an update
        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", youngest),
                ("overwrite", youngest),
            ])
        )

    @inline_callbacks
    def test_update_with_no_ancestor(self):
        """
        Give the updater a remote update with no ancestors
        """

        parent_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('a' * 26, 'a' * 52)
        parent = RemoteSnapshot(
            name="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[parent_cap] = parent

        child_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('b' * 26, 'b' * 52)
        child = RemoteSnapshot(
            name="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=child_cap,
            parents_raw=[parent_cap],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[child_cap] = child

        other_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('z' * 26, 'z' * 52)
        other = RemoteSnapshot(
            name="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=other_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[other_cap] = other

        # so "alice" has "other" already
        self.alice_magic_path.child("foo").setContent("whatever")
        self.alice_config.store_remotesnapshot("foo", other)

        # ...child->parent aren't related to "other"
        yield self.updater.add_remote_snapshot(child)

        # so, no common ancestor: a conflict
        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", child),
                ("conflict", child),
            ])
        )

    @inline_callbacks
    def test_old_update(self):
        """
        An update that's older than our local one
        """

        parent_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('a' * 26, 'a' * 52)
        parent = RemoteSnapshot(
            name="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[parent_cap] = parent

        child_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('b' * 26, 'b' * 52)
        child = RemoteSnapshot(
            name="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=child_cap,
            parents_raw=[parent_cap],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[child_cap] = child

        # so "alice" has "child" already
        self.alice_magic_path.child("foo").setContent("whatever")
        self.alice_config.store_remotesnapshot("foo", child)

        # we update with the parent (so, it's old)
        yield self.updater.add_remote_snapshot(parent)

        # so we should do nothing
        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", parent),
            ])
        )
