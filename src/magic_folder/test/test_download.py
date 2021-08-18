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
import time
from mock import Mock

from json import (
    dumps,
    loads,
)

from hyperlink import (
    DecodedURL,
)

from eliot.twisted import (
    inline_callbacks,
)

from testtools.matchers import (
    MatchesAll,
    MatchesListwise,
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
    Clock,
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
    DownloaderService,
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
    FolderStatus,
    WebSocketStatusService,
)
from ..tahoe_client import (
    create_tahoe_client,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from ..participants import (
    participants_from_collective,
)
from ..util.file import (
    PathState,
    get_pathinfo,
    seconds_to_ns,
)
from .common import (
    SyncTestCase,
    AsyncTestCase,
)
from .matchers import matches_flushed_traceback
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
            None,
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
        relpath = "foo"
        metadata = {
            "snapshot_version": 1,
            "relpath": relpath,
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
                                            relpath,
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
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": Equals([]),
                        "relpath": Equals(relpath),
                    }),
                )
            )
        )

    def test_cache_parents(self):
        """
        Caching a RemoteSnapshot with parents works
        """
        self.setup_example()
        relpath = "foo"
        parents = []
        genesis = None

        for who in range(5):
            metadata = {
                "snapshot_version": 1,
                "relpath": relpath,
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
                                                    relpath,
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
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": Equals([]),
                        "relpath": Equals(relpath),
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
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": AfterPreprocessing(len, Equals(1)),
                        "relpath": Equals(relpath),
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
                    metadata=ContainsDict({
                        "snapshot_version": Equals(1),
                        "parents": AfterPreprocessing(len, Equals(1)),
                        "relpath": Equals(relpath),
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
            None,
        )
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text("http://invalid./"),
            self.http_client,
        )
        self.status_service = WebSocketStatusService(reactor, self._global_config)
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
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content)),
            )
        )

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
        self.assertThat(
            self.magic_path.child("foo.conflict-zara"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content)),
            )
        )
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(original_content)),
            )
        )

    @inlineCallbacks
    def test_update(self):
        """
        Create a snapshot in zara's Personal DMD, then update it.
        """
        # Use a time slightly in the past so that it we can
        # test that it is written to the filesystem
        modified_time = int(time.time()) - 60

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
            modified_time=modified_time,
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
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content1)),
            )
        )
        self.assertThat(
            self.config.get_currentsnapshot_pathstate("foo"),
            MatchesAll(
                Equals(
                    get_pathinfo(self.magic_path.child("foo")).state,
                ),
                MatchesStructure.byEquality(
                    # We don't check the ctime here, since we can't control it.
                    mtime_ns=seconds_to_ns(modified_time),
                    size=len(content1),
                ),
            )
        )

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
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content1)),
            )
        )

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
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content2)),
            )
        )

    @inlineCallbacks
    def test_conflicting_update(self):
        """
        Create an update that conflicts with a local file
        """

        content0 = b"foo" * 1000
        content1 = b"bar" * 1000
        content2 = b"baz" * 1000
        content3 = b"quux" * 1000
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

        # just before we link this in, put a local change "in the way"
        # on Alice's side.
        self.magic_path.child("foo").setContent(content2)

        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap1.capability.encode("utf8"),
            replace=True,
        )

        # wait for the downloader to put this into Alice's
        # magic-folder, which should cause a conflcit
        for _ in range(10):
            if "foo.conflict-zara" in self.magic_path.listdir():
                break
            yield deferLater(reactor, 1.0, lambda: None)
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content2)),
            )
        )

        # now we produce another update on Zara's side, which
        # shouldn't change anything locally because we're already
        # conflicted.

        # create an update
        local_snap2 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content3),
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

        for _ in range(10):
            if "foo.conflict-zara" in self.magic_path.listdir():
                if self.magic_path.child("foo.conflict-zara").getContent() != content1:
                    print("weird content")
            yield deferLater(reactor, 1.0, lambda: None)
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content2)),
            )
        )


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

        self.alice_collective = b"URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra"
        self.alice_personal = b"URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra"

        self.alice_config = self._global_config.create_magic_folder(
            "default",
            self.alice_magic_path,
            self.alice,
            self.alice_collective,
            self.alice_personal,
            1,
            None,
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

        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            StubTreq(StringStubbingResource(get_resource_for)),
        )
        particiapnts = participants_from_collective(
            self.alice_collective, self.alice_personal, tahoe_client
        )
        self.status = WebSocketStatusService(reactor, self._global_config)
        self.updater = MagicFolderUpdater(
            magic_fs=self.filesystem,
            config=self.alice_config,
            remote_cache=self.remote_cache,
            tahoe_client=tahoe_client,
            status=FolderStatus(self.alice_config.name, self.status),
            write_participant=particiapnts.writer,
        )

    @inline_callbacks
    def test_update_with_local(self):
        """
        Give the updater a remote update while we also have a LocalSnapshot
        """

        cap0 = b"URI:DIR2-CHK:aaaaaaaaaaaaaaaaaaaaaaaaaa:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:1:5:376"
        remote0 = RemoteSnapshot(
            relpath="foo",
            author=self.carol,
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[cap0] = remote0

        local0_content = b"dummy content"
        local0 = yield create_snapshot(
            relpath="foo",
            author=self.alice,
            data_producer=io.BytesIO(local0_content),
            snapshot_stash_dir=self.state_path,
            parents=None,
            raw_remote_parents=None,
        )
        # if we have a local, we must have the path locally
        local_path = self.alice_magic_path.child("foo")
        local_path.setContent(local0_content)
        self.alice_config.store_local_snapshot(local0)
        self.alice_config.store_currentsnapshot_state("foo", get_pathinfo(local_path).state)

        # tell the updater to examine the remote-snapshot
        yield self.updater.add_remote_snapshot("foo", remote0)

        # we have a local-snapshot for the same relpath as the incoming
        # remote, so this is a conflict

        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", "foo", remote0.content_cap),
                ("conflict", "foo", "foo.conflict-{}".format(self.carol.name), remote0.content_cap),
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
            relpath="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        parent_content = b"parent" * 1000
        self.remote_cache._cached_snapshots[parent_cap] = parent
        # we've 'seen' this file before so we must have the path locally
        local_path = self.alice_magic_path.child("foo")
        local_path.setContent(parent_content)
        self.alice_config.store_downloaded_snapshot("foo", parent, get_pathinfo(local_path).state)

        cap0 = b"URI:DIR2-CHK:aaaaaaaaaaaaaaaaaaaaaaaaaa:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:1:5:376"
        remote0 = RemoteSnapshot(
            relpath="foo",
            author=self.carol,
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[parent_cap],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[cap0] = remote0


        # tell the updater to examine the remote-snapshot
        yield self.updater.add_remote_snapshot("foo", remote0)

        # we have a local-snapshot for the same relpath as the incoming
        # remote, so this is a conflict

        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", "foo", remote0.content_cap),
                ("overwrite", "foo", remote0.content_cap),
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
                relpath="foo",
                author=self.alice,
                metadata={"modification_time": 0},
                capability=parent_cap,
                parents_raw=[] if not remotes else [remotes[-1].capability],
                content_cap=b"URI:CHK:",
            )
            self.remote_cache._cached_snapshots[parent_cap] = parent
            remotes.append(parent)

        # set "our" parent to the oldest one
        local_path = self.alice_magic_path.child("foo")
        # we've 'seen' this file before so we must have the path locally
        local_path.setContent(b"dummy")
        self.alice_config.store_downloaded_snapshot("foo", remotes[0], get_pathinfo(local_path).state)

        # tell the updater to examine the youngest remote
        youngest = remotes[-1]
        yield self.updater.add_remote_snapshot("foo", youngest)

        # we have a common ancestor so this should be an update
        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", "foo", youngest.content_cap),
                ("overwrite", "foo", youngest.content_cap),
            ])
        )

    @inline_callbacks
    def test_update_with_no_ancestor(self):
        """
        Give the updater a remote update with no ancestors
        """

        parent_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('a' * 26, 'a' * 52)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[parent_cap] = parent

        child_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('b' * 26, 'b' * 52)
        child = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=child_cap,
            parents_raw=[parent_cap],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[child_cap] = child

        other_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('z' * 26, 'z' * 52)
        other = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=other_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[other_cap] = other

        # so "alice" has "other" already
        self.alice_magic_path.child("foo").setContent("whatever")
        self.alice_config.store_downloaded_snapshot("foo", other, PathState(
            mtime_ns=0, ctime_ns=0, size=len("whatever"),
        ))

        # ...child->parent aren't related to "other"
        yield self.updater.add_remote_snapshot("foo", child)

        # so, no common ancestor: a conflict
        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", "foo", child.content_cap),
                ("conflict", "foo", "foo.conflict-{}".format(self.alice.name), child.content_cap),
            ])
        )

    @inline_callbacks
    def test_old_update(self):
        """
        An update that's older than our local one
        """

        parent_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('a' * 26, 'a' * 52)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"relpath": "foo", "modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[parent_cap] = parent

        child_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('b' * 26, 'b' * 52)
        child = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"relpath": "foo", "modification_time": 0},
            capability=child_cap,
            parents_raw=[parent_cap],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[child_cap] = child

        # so "alice" has "child" already
        local_path = self.alice_magic_path.child("foo")
        local_path.setContent("whatever")
        self.alice_config.store_downloaded_snapshot("foo", child, get_pathinfo(local_path).state)

        # we update with the parent (so, it's old)
        yield self.updater.add_remote_snapshot("foo", parent)

        # so we should do nothing
        self.assertThat(
            self.filesystem.actions,
            Equals([
            ])
        )

    @inline_callbacks
    def test_update_filesystem_error(self):
        """
        An update that fails to write to the filesystem
        """

        parent_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('a' * 26, 'a' * 52)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"relpath": "foo", "modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[parent_cap] = parent

        def permissions_suck(relpath, mtime, staged_content):
            """
            cause the filesystem to fail to write due to a permissions problem
            """
            raise OSError(13, "Permission denied")
        self.filesystem.mark_overwrite = permissions_suck

        # set up a collective for 'alice' to pull an update from
        # 'carol' into the "always permissions denied" filesystem
        # .. but it needs to be "real" since we have to run the
        # top-level scanner which discovers snapshots (because it
        # handles the top-level errors)
        root = create_fake_tahoe_root()
        client = create_tahoe_treq_client(root)
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text("http://invalid./"),
            client,
        )
        collective = yield tahoe_client.create_mutable_directory()
        alice_personal = yield tahoe_client.create_mutable_directory()
        carol_personal = yield tahoe_client.create_mutable_directory()
        yield tahoe_client.add_entry_to_mutable_directory(collective, "carol", carol_personal)
        yield tahoe_client.add_entry_to_mutable_directory(carol_personal, "foo", parent.capability)

        alice_participants = participants_from_collective(
            collective,
            alice_personal,
            tahoe_client,
        )

        # hook in the top-level service .. we don't "start" it and
        # instead just call _loop() because we just want a single
        # scan.
        top_service = DownloaderService(
            Clock(),
            self.alice_config,
            alice_participants,
            self.updater._status,
            self.remote_cache,
            self.updater,
            tahoe_client,
        )
        yield top_service._loop()

        # status system should report our error
        self.assertThat(
            loads(self.status._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Failed to overwrite file 'foo': [Errno 13] Permission denied"
                                    ),
                                }),
                            ),
                        }),
                    }),
                }),
            })
        )

        self.assertThat(
            self.eliot_logger.flush_tracebacks(OSError),
            MatchesListwise([
                matches_flushed_traceback(OSError, r"\[Errno 13\] Permission denied")
            ]),
        )

    @inline_callbacks
    def test_update_download_error(self):
        """
        An update that fails to download some content
        """

        parent_cap = b"URI:DIR2-CHK:{}:{}:1:5:376".format('a' * 26, 'a' * 52)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=b"URI:CHK:",
        )
        self.remote_cache._cached_snapshots[parent_cap] = parent

        def network_is_out(relpath, file_cap, tahoe_client):
            """
            download fails for some reason
            """
            raise Exception("something bad")
        self.filesystem.download_content_to_staging = network_is_out

        # set up a collective for 'alice' to pull an update from
        # 'carol' into the "always permissions denied" filesystem
        # .. but it needs to be "real" since we have to run the
        # top-level scanner which discovers snapshots (because it
        # handles the top-level errors)
        root = create_fake_tahoe_root()
        client = create_tahoe_treq_client(root)
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text("http://invalid./"),
            client,
        )
        collective = yield tahoe_client.create_mutable_directory()
        alice_personal = yield tahoe_client.create_mutable_directory()
        carol_personal = yield tahoe_client.create_mutable_directory()
        yield tahoe_client.add_entry_to_mutable_directory(collective, "carol", carol_personal)
        yield tahoe_client.add_entry_to_mutable_directory(carol_personal, "foo", parent.capability)

        alice_participants = participants_from_collective(
            collective,
            alice_personal,
            tahoe_client,
        )

        # hook in the top-level service .. we don't "start" it and
        # instead just call _loop() because we just want a single
        # scan.
        top_service = DownloaderService(
            Clock(),
            self.alice_config,
            alice_participants,
            self.updater._status,
            self.remote_cache,
            self.updater,
            tahoe_client,
        )
        yield top_service._loop()

        # status system should report our error
        self.assertThat(
            loads(self.status._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": Equals([
                                {
                                    "timestamp": int(self.status._clock.seconds()),
                                    "summary": "Failed to download snapshot for 'foo'.",
                                },
                            ]),
                        }),
                    }),
                }),
            })
        )

        self.assertThat(
            self.eliot_logger.flush_tracebacks(Exception),
            MatchesListwise([
                matches_flushed_traceback(Exception, "something bad")
            ]),
        )
