"""
Tests relating generally to magic_folder.downloader
"""

import io
import base64
import time
from mock import (
    patch,
)

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
    NotEquals,
    ContainsDict,
    AfterPreprocessing,
)
from testtools import (
    ExpectedException,
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
    Deferred,
    returnValue,
    succeed,
)
from twisted.python.filepath import (
    FilePath,
)
from treq.testing import (
    StringStubbingResource,
)

from ..config import (
    create_testing_configuration,
)
from ..downloader import (
    RemoteSnapshotCacheService,
    InMemoryMagicFolderFilesystem,
    RemoteScannerService,
    LocalMagicFolderFilesystem,
    BackupRetainedError,
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
from ..participants import (
    participants_from_collective,
)
from ..util.capabilities import (
    Capability,
    random_immutable,
)
from ..util.file import (
    PathState,
    get_pathinfo,
    seconds_to_ns,
)
from ..util.wrap import (
    wrap_frozen,
)
from ..util.twisted import (
    cancelled,
)
from .common import (
    SyncTestCase,
    AsyncTestCase,
)
from .matchers import (
    matches_flushed_traceback,
)
from .strategies import (
    tahoe_lafs_immutable_dir_capabilities,
)
from .fixtures import (
    MagicFolderNode,
    TahoeClientWrapper,
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
        self.collective_cap = Capability.from_string("URI:DIR2:mfqwcylbmfqwcylbmfqwcylbme:mfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqwcylbmfqq")
        self.personal_cap = Capability.from_string("URI:DIR2:mjrgeytcmjrgeytcmjrgeytcmi:mjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjrgeytcmjra")

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
        _, metadata_cap = self.root.add_data("URI:CHK:", dumps(metadata).encode("utf8"))

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
                                            Capability.from_string(content_cap),
                                            Capability.from_string(metadata_cap),
                                        ).signature
                                    ).decode("utf8"),
                                }
                            },
                            "size": 246
                        }
                    ]
                }
            }
        ]).encode("utf8")
        _, cap = self.root.add_data("URI:DIR2-CHK:", data)

        # we've set up some fake data; lets see if the cache service
        # will cache this successfully.
        self.assertThat(
            self.cache.get_snapshot_from_capability(Capability.from_string(cap)),
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
            metadata_cap = self.root.add_data("URI:CHK:", dumps(metadata).encode("utf8"))[1]

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
                                                    Capability.from_string(content_cap),
                                                    Capability.from_string(metadata_cap),
                                                ).signature
                                            ).decode("ascii"),
                                        }
                                    },
                                    "size": 246
                                }
                            ]
                        }
                    }
                ]).encode("utf8")
            )
            parents = [cap]
            if genesis is None:
                genesis = Capability.from_string(cap)

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
            self.cache.get_snapshot_from_capability(Capability.from_string(cap)),
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
        # the already-cached tree of Snapshots ..

        def all_requests_are_error(*args, **kw):
            raise RuntimeError("nothing should call me")

        with patch.object(self.cache, "tahoe_client", StringStubbingResource(all_requests_are_error)):
            self.assertThat(
                self.cache.get_snapshot_from_capability(Capability.from_string(cap)),
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
        _, personal_raw_cap = self.root.add_data(
            "URI:DIR2:",
            dumps([
                "dirnode",
                {
                    "children": {}
                },
            ]).encode("utf8"),
        )
        self.personal_cap = Capability.from_string(personal_raw_cap)
        _, other_personal_raw_cap = self.root.add_data(
            "URI:DIR2:",
            dumps([
                "dirnode",
                {
                    "children": {},
                },
            ]).encode("utf8"),
        )
        self.other_personal_cap = Capability.from_string(other_personal_raw_cap)

        # create the Collective DMD with both alice and zara
        _, collective_raw_cap = self.root.add_data(
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
        self.collective_cap = Capability.from_string(collective_raw_cap)

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

    @inline_callbacks
    def tearDown(self):
        yield super(UpdateTests, self).tearDown()
        yield self.service.file_factory.finish()
        yield self.service.stopService()

    @inline_callbacks
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
            remote_snap.capability,
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

    @inline_callbacks
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
            remote_snap.capability,
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

    @inline_callbacks
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
            remote_snap0.capability,
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
            remote_snap1.capability,
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

    @inline_callbacks
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
            remote_snap0.capability,
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
            remote_snap1.capability,
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
            remote_snap2.capability,
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

    @inline_callbacks
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
            remote_snap0.capability,
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
            remote_snap1.capability,
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
            remote_snap2.capability,
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

    @inline_callbacks
    def test_conflict_at_last_second(self):
        """
        If an update to a file happens while we're downloading a file that
        is detected as a conflict.
        """

        content0 = b"foo" * 1000
        local_snap0 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content0),
            self.state_path,
        )

        # arrange to hook ourselves in to the "download" code-path
        # immediately _after_ the download has occurred...

        # "our" folder updater service
        file_factory = self.service.file_factory
        orig_method = file_factory._magic_fs.download_content_to_staging

        @inline_callbacks
        def do_download(relpath, cap, tahoe_client):
            """
            wrap the "download_content_to_staging" method so we can inject a
            last-second change to file "foo" _immediately_ after we
            download zara's update (but before we act on it)
            """
            x = yield orig_method(relpath, cap, tahoe_client)
            # put in the last-second conflict
            self.magic_path.preauthChild(relpath).setContent(b"So conflicted")
            returnValue(x)

        with patch.object(file_factory._magic_fs, "download_content_to_staging", do_download):
            # create a change in zara's Personal DMD
            remote_snap0 = yield write_snapshot_to_tahoe(local_snap0, self.other, self.tahoe_client)
            yield self.tahoe_client.add_entry_to_mutable_directory(
                self.other_personal_cap,
                u"foo",
                remote_snap0.capability,
            )

            # wait for the downloader to detect zara's change
            for _ in range(10):
                yield deferLater(reactor, 1.0, lambda: None)
                if self.magic_path.listdir() == ['foo.conflict-zara', 'foo']:
                    break

        # we should discover and mark this last-second conflict by
        # keeping "our" content in "foo" and putting zara's content in
        # a conflict-file
        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(b"So conflicted")),
            )
        )
        self.assertThat(
            self.magic_path.child("foo.conflict-zara"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content0)),
            )
        )

    @inline_callbacks
    def test_conflict_at_really_the_last_second(self):
        """
        There is a window between _download_check_local and the point in
        the actual call to .mark_overwrite when other procesess could
        have written to our local file (or its tempfile).

        To test this, we hook into the state-machine to cause such a
        write _immediately after_ the machine identifies "continue
        with the update"
        """

        start = set(self.root._uri.data.keys())
        # make some existing content for file "foo"
        content0 = b"original" * 1000
        with self.magic_path.child("foo").open("w") as f:
            f.write(content0)

        # wait for the uploader to do its work (that is, for more data
        # to appear in "tahoe")
        yield self.service.scanner_service.scan_once()
        for _ in range(10):
            yield deferLater(reactor, 1.0, lambda: None)
            if len(self.root._uri.data.keys()) != len(start):
                break

        # create a (legitimate) update from zara to the same file (so
        # that our downloader has some work to do)
        content1 = b"foo" * 1000
        local_snap1 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content1),
            self.state_path,
            raw_remote_parents=[
                self.config.get_remotesnapshot("foo").danger_real_capability_string(),
            ],
        )

        # arrange to hook ourselves in to the "download" code-path
        # immediately _after_ the state-transition out of
        # _download_checking_local happens -- putting us inside the
        # "very last millisecond" window for other writes
        mf = self.service.file_factory.magic_file_for(self.magic_path.child("foo"))

        orig = mf._download_matches

        def wrap(*args, **kw):
            with self.magic_path.child("foo").open("w") as f:
                f.write(b"last-second change")
            return orig(*args, **kw)
        mf._download_matches = wrap

        # create the change in zara's Personal DMD (that is, as if her
        # instance had done an upload)
        remote_snap0 = yield write_snapshot_to_tahoe(local_snap1, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap0.capability,
        )

        # now, we download zara's change but our wrapped
        # _download_matches @input method makes a last-millisecond
        # change in the window between the state-machine's check and
        # the running of the mark_overwrite function, essentially
        # .. this should then rename the preserved tempfile as a
        # conflict-file
        for _ in range(10):
            yield deferLater(reactor, 1.0, lambda: None)
            if self.magic_path.listdir() == ['foo.conflict-zara', 'foo']:
                break

        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content1)),
            )
        )
        self.assertThat(
            self.magic_path.child("foo.conflict-zara"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(b"last-second change")),
            )
        )
        # we should also generate a user-visible error message
        self.assertThat(
            self.eliot_logger.flush_tracebacks(BackupRetainedError),
            MatchesListwise([
                matches_flushed_traceback(
                    BackupRetainedError,
                    ".*unexpected modification-time.*",
                )
            ]),
        )

    @inline_callbacks
    def test_conflict_at_really_the_last_second_no_local(self):
        """
        Same as above check but without an existing local file at all.
        """

        # create a (legitimate) update from zara to non-existant local
        # file "foo"
        content1 = b"foo" * 1000
        local_snap1 = yield create_snapshot(
            "foo",
            self.other,
            io.BytesIO(content1),
            self.state_path,
        )

        # arrange to hook ourselves in to the "download" code-path
        # immediately _after_ the state-transition out of
        # _download_checking_local happens -- putting us inside the
        # "very last millisecond" window for other writes
        mf = self.service.file_factory.magic_file_for(self.magic_path.child("foo"))

        orig = mf._download_matches

        def wrap(*args, **kw):
            with self.magic_path.child("foo").open("w") as f:
                f.write(b"last-second change")
            return orig(*args, **kw)
        mf._download_matches = wrap

        # create the change in zara's Personal DMD (that is, as if her
        # instance had done an upload)
        remote_snap0 = yield write_snapshot_to_tahoe(local_snap1, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            u"foo",
            remote_snap0.capability,
        )

        # now, we download zara's change but our wrapped
        # _download_matches @input method makes a last-millisecond
        # change in the window between the state-machine's check and
        # the running of the mark_overwrite function, essentially
        # .. this should then rename the preserved tempfile as a
        # conflict-file
        for _ in range(10):
            yield deferLater(reactor, 1.0, lambda: None)
            if self.magic_path.listdir() == ['foo.conflict-zara', 'foo']:
                break

        self.assertThat(
            self.magic_path.child("foo"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(content1)),
            )
        )
        self.assertThat(
            self.magic_path.child("foo.conflict-zara"),
            MatchesAll(
                AfterPreprocessing(lambda x: x.exists(), Equals(True)),
                AfterPreprocessing(lambda x: x.getContent(), Equals(b"last-second change")),
            )
        )
        # we should also generate a user-visible error message
        self.assertThat(
            self.eliot_logger.flush_tracebacks(BackupRetainedError),
            MatchesListwise([
                matches_flushed_traceback(
                    BackupRetainedError,
                    ".*unexpected modification-time.*",
                )
            ]),
        )

    @inline_callbacks
    def test_state_mismatch(self):
        """
        If the database-stored pathstate doesn't match what's on disk when
        we receive an update a conflict results.
        """

        relpath = "a"

        # give alice current knowledge of this file
        local_path = self.magic_path.child(relpath)
        local_path.setContent(b"dummy contents")

        alice_snap = yield create_snapshot(
            relpath,
            self.author,
            io.BytesIO(b"dummy contents"),
            self.state_path,
        )
        current_pathstate = get_pathinfo(local_path).state
        alice_remote = yield write_snapshot_to_tahoe(alice_snap, self.author, self.tahoe_client)
        # mess with the time, so it doesn't match what's on disk
        self.config.store_currentsnapshot_state(
            relpath,
            PathState(
                current_pathstate.size,
                current_pathstate.mtime_ns + 60000000,
                current_pathstate.ctime_ns + 60000000,
            )
        )

        # zara creates a snapshot
        content0 = b"zara was here" * 1000
        local_snap0 = yield create_snapshot(
            relpath,
            self.other,
            io.BytesIO(content0),
            self.state_path,
            raw_remote_parents=[alice_remote.capability.danger_real_capability_string()],
        )

        # create a change in zara's Personal DMD
        remote_snap0 = yield write_snapshot_to_tahoe(local_snap0, self.other, self.tahoe_client)
        yield self.tahoe_client.add_entry_to_mutable_directory(
            self.other_personal_cap,
            relpath,
            remote_snap0.capability,
        )

        # wait for the downloader to detect zara's change
        expected_files = {
            "{}.conflict-zara".format(relpath),
            relpath,
        }
        for _ in range(15):
            yield deferLater(reactor, 1.0, lambda: None)
            if set(self.magic_path.listdir()) == expected_files:
                break

        self.assertThat(
            set(self.magic_path.listdir()),
            Equals(expected_files),
        )


class ConflictTests(AsyncTestCase):
    """
    Tests relating to conflict cases
    """

    def setUp(self):
        super(ConflictTests, self).setUp()

        self.alice_magic_path = FilePath(self.mktemp())
        self.alice_magic_path.makedirs()
        self.alice = MagicFolderNode.create(
            reactor,
            FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": self.alice_magic_path,
                    "author-name": "alice",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                },
            },
            start_folder_services=True,
        )

        self.file_factory = self.alice.global_service.getServiceNamed("magic-folder-default").file_factory
        self.remote_cache = self.file_factory._remote_cache
        self.state_path = self.alice.global_config._get_state_path("default")
        self.alice_config = self.alice.global_config.get_magic_folder("default")
        self.alice_author = self.alice_config.author
        self.filesystem = InMemoryMagicFolderFilesystem()
        self.file_factory._magic_fs = self.filesystem

        self.carol_author = create_local_author("carol")

    def tearDown(self):
        super(ConflictTests, self).tearDown()
        return self.alice.cleanup()

    @inline_callbacks
    def test_update_with_local(self):
        """
        Give the updater a remote update while we also have a LocalSnapshot
        """

        cap0 = random_immutable(directory=True)
        remote0 = RemoteSnapshot(
            relpath="foo",
            author=self.carol_author,
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[cap0.danger_real_capability_string()] = remote0

        local0_content = b"dummy content"
        local0 = yield create_snapshot(
            relpath="foo",
            author=self.alice_author,
            data_producer=io.BytesIO(local0_content),
            snapshot_stash_dir=self.state_path,
            parents=None,
            raw_remote_parents=None,
        )
        # if we have a local, we must have the path locally
        local_path = self.alice_magic_path.child("foo")
        local_path.setContent(local0_content)
        self.alice_config.store_local_snapshot(
            local0,
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )
        self.alice_config.store_currentsnapshot_state("foo", get_pathinfo(local_path).state)

        # tell the state-machine about the local, and then get it to
        # examine the remote-snapshot
        mf = self.file_factory.magic_file_for(local_path)
        yield mf.local_snapshot_exists(local0)
        yield mf.found_new_remote(remote0)

        yield mf.when_idle()

        # we have a local-snapshot for the same relpath as the incoming
        # remote, so this is a conflict
        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", "foo", remote0.content_cap),
                ("conflict", "foo", "foo.conflict-carol", remote0.content_cap),
            ])
        )

    @inline_callbacks
    def test_update_with_parent(self):
        """
        Give the updater a remote update that has a parent which is 'our'
        snapshot
        """

        parent_cap = random_immutable(directory=True)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        parent_content = b"parent" * 1000
        self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent
        # we've 'seen' this file before so we must have the path locally
        local_path = self.alice_magic_path.child("foo")
        local_path.setContent(parent_content)
        self.alice_config.store_downloaded_snapshot("foo", parent, get_pathinfo(local_path).state)

        cap0 = random_immutable(directory=True)
        remote0 = RemoteSnapshot(
            relpath="foo",
            author=self.carol_author,
            metadata={"modification_time": 0},
            capability=cap0,
            parents_raw=[parent_cap.danger_real_capability_string()],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[cap0.danger_real_capability_string()] = remote0

        # tell the updater to examine the remote-snapshot
        mf = self.file_factory.magic_file_for(local_path)
        yield mf.found_new_remote(remote0)
        yield mf.when_idle()

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

        for _ in range(4):
            parent_cap = random_immutable(directory=True)
            parent = RemoteSnapshot(
                relpath="foo",
                author=self.alice_author,
                metadata={"modification_time": 0},
                capability=parent_cap,
                parents_raw=[] if not remotes else [remotes[-1].capability.danger_real_capability_string()],
                content_cap=random_immutable(),
                metadata_cap=random_immutable(),
            )
            self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent
            remotes.append(parent)

        # set "our" parent to the oldest one
        local_path = self.alice_magic_path.child("foo")
        # we've 'seen' this file before so we must have the path locally
        local_path.setContent(b"dummy")
        self.alice_config.store_downloaded_snapshot("foo", remotes[0], get_pathinfo(local_path).state)

        # tell the updater to examine the youngest remote
        youngest = remotes[-1]
        mf = self.file_factory.magic_file_for(local_path)
        yield mf.found_new_remote(youngest)
        yield mf.when_idle()

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

        parent_cap = random_immutable(directory=True)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent

        child_cap = random_immutable(directory=True)
        child = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"modification_time": 0},
            capability=child_cap,
            parents_raw=[parent_cap.danger_real_capability_string()],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[child_cap.danger_real_capability_string()] = child

        other_cap = random_immutable(directory=True)
        other = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"modification_time": 0},
            capability=other_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[other_cap.danger_real_capability_string()] = other

        # so "alice" has "other" already
        self.alice_magic_path.child("foo").setContent(b"whatever")
        self.alice_config.store_downloaded_snapshot("foo", other, PathState(
            mtime_ns=0, ctime_ns=0, size=len("whatever"),
        ))

        # ...child->parent aren't related to "other"
        mf = self.file_factory.magic_file_for(self.alice_magic_path.child("foo"))
        yield mf.found_new_remote(child)
        yield mf.when_idle()

        # so, no common ancestor: a conflict
        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("download", "foo", child.content_cap),
                ("conflict", "foo", "foo.conflict-alice", child.content_cap),
            ])
        )

    @inline_callbacks
    def test_update_delete(self):
        """
        Give the updater a remote update which is a delete
        """

        parent_cap = random_immutable(directory=True)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent

        child_cap = random_immutable(directory=True)
        child = RemoteSnapshot(
            relpath="foo",
            author=self.alice,
            metadata={"modification_time": 0},
            capability=child_cap,
            parents_raw=[parent_cap.danger_real_capability_string()],
            content_cap=None,
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[child_cap.danger_real_capability_string()] = child

        class FakeWriteParticipant(object):
            def update_snapshot(self, relpath, cap):
                return succeed(None)
        self.file_factory._write_participant = FakeWriteParticipant()

        # so "alice" has "parent" already
        self.alice_magic_path.child("foo").setContent(b"whatever")
        self.alice_config.store_downloaded_snapshot(
            "foo",
            parent,
            get_pathinfo(self.alice_magic_path.child("foo")).state,
        )

        # deletion snapshot
        mf = self.file_factory.magic_file_for(self.alice_magic_path.child("foo"))
        yield mf.found_new_remote(child)
        yield mf.when_idle()

        self.assertThat(
            self.filesystem.actions,
            Equals([
                ("delete", "foo"),
            ])
        )

    @inline_callbacks
    def test_old_update(self):
        """
        An update that's older than our local one
        """

        parent_cap = random_immutable(directory=True)
        parent_content_cap = random_immutable()
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"relpath": "foo", "modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=parent_content_cap,
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent

        child_cap = random_immutable(directory=True)
        child = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"relpath": "foo", "modification_time": 0},
            capability=child_cap,
            parents_raw=[parent_cap.danger_real_capability_string()],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[child_cap.danger_real_capability_string()] = child

        # so "alice" has "child" already
        local_path = self.alice_magic_path.child("foo")
        local_path.setContent(b"whatever")
        self.alice_config.store_downloaded_snapshot("foo", child, get_pathinfo(local_path).state)

        # we update with the parent (so, it's old)
        mf = self.file_factory.magic_file_for(local_path)
        yield mf.found_new_remote(parent)
        yield mf.when_idle()

        # XXX to fix this need to add another state in the
        # state-machine to "maybe bail out early" if the updater is
        # newer before _begin_download()

        # so we should do nothing
        self.assertThat(
            self.filesystem.actions,
            Equals([
                (u'download', u'foo', parent_content_cap),
            ])
        )

    @inline_callbacks
    def test_update_filesystem_error(self):
        """
        An update that fails to write to the filesystem
        """

        parent_cap = random_immutable(directory=True)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"relpath": "foo", "modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent

        def permissions_suck(relpath, mtime, staged_content, prior_pathstate):
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
        tahoe_client = self.alice.tahoe_client
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
        top_service = RemoteScannerService(
            Clock(),
            self.alice_config,
            alice_participants,
            self.file_factory,
            self.remote_cache,
            self.alice.global_service.status_service,
        )
        yield top_service._loop()
        yield self.file_factory.finish()

        # status system should report our error
        self.assertThat(
            loads(self.alice.global_service.status_service._marshal_state()),
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

        parent_cap = random_immutable(directory=True)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent

        fails = [object()]
        orig_download = self.filesystem.download_content_to_staging

        def network_is_out(relpath, file_cap, tahoe_client):
            """
            download fails for some reason

            We only want a single error; if we let the network
            "always" be down then the `yield top_service._loop()` call
            below will never succeed because it'll just keep re-
            trying.
            """
            if not fails:
                return orig_download(relpath, file_cap, tahoe_client)
            fails.pop(0)
            raise Exception("the network is down")
        self.filesystem.download_content_to_staging = network_is_out

        # set up a collective for 'alice' to pull an update from
        # 'carol' into the "always permissions denied" filesystem
        # .. but it needs to be "real" since we have to run the
        # top-level scanner which discovers snapshots (because it
        # handles the top-level errors)
        tahoe_client = self.alice.tahoe_client
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
        top_service = RemoteScannerService(
            Clock(),
            self.alice_config,
            alice_participants,
            self.file_factory,
            self.remote_cache,
            self.alice.global_service.status_service,
        )
        yield top_service._loop()

        # status system should report our error
        self.assertThat(
            loads(self.alice.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Failed to download snapshot for 'foo'."
                                    )
                                }),
                            ),
                        }),
                    }),
                }),
            })
        )

        self.assertThat(
            self.eliot_logger.flush_tracebacks(Exception),
            MatchesListwise([
                matches_flushed_traceback(Exception, "the network is down"),
            ]),
        )

    @inline_callbacks
    def test_fail_download_dmd_update(self):
        """
        An update arrives but we fail to update our Personal DMD
        """

        parent_cap = random_immutable(directory=True)
        parent = RemoteSnapshot(
            relpath="foo",
            author=self.alice_author,
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=Capability.from_string(
                self.alice.tahoe_root.add_data("URI:CHK:", b"fake content")[1]
            ),
            metadata_cap=Capability.from_string(
                self.alice.tahoe_root.add_data("URI:CHK:", b"{}")[1]
            ),
        )
        self.remote_cache._cached_snapshots[parent_cap.danger_real_capability_string()] = parent

        tahoe_client = self.alice.tahoe_client
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

        # now that we're set up, arrange to fail the next "add a thing
        # to a dir" call
        self.alice.tahoe_root.fail_next_directory_update()

        # hook in the top-level service .. we don't "start" it and
        # instead just call _loop() because we just want a single
        # scan.
        top_service = RemoteScannerService(
            Clock(),
            self.alice_config,
            alice_participants,
            self.file_factory,
            self.remote_cache,
            self.alice.global_service.status_service,
        )
        yield top_service._loop()

        # status system should report our error
        self.assertThat(
            loads(self.alice.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Error updating personal DMD: Couldn't add foo to directory. Error code 500"
                                    )
                                }),
                            ),
                        }),
                    }),
                }),
            })
        )

        self.assertThat(
            self.eliot_logger.flush_tracebacks(Exception),
            MatchesListwise([
                matches_flushed_traceback(Exception, "Couldn't add foo to directory. Error code 500")
            ]),
        )


class CancelTests(AsyncTestCase):
    """
    Tests relating to cancelling operations
    """

    # XXX NOTE if this name gets longer, the resulting temp-paths can
    # become "too long" on windows causing failures
    @inline_callbacks
    def test_cancel0(self):
        """
        An update arrives but one of the tahoe requests is cancelled
        """
        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "some_file"

        carol = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "carol",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            tahoe_client=TahoeClientWrapper(
                stream_capability=cancelled,
            ),
            start_folder_services=True,
        )
        self.addCleanup(carol.cleanup)

        service = carol.global_service.get_folder_service("default")

        local = magic_path.child(relpath)
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        class FakeRemoteSnapshot(object):
            content_cap = random_immutable(directory=True)
            relpath = "some_file"  # match earlier relpath
        remote_snapshot = FakeRemoteSnapshot()

        mf = service.file_factory.magic_file_for(local)
        # when .stream_capability() is called it will receive an
        # already cancelled Deferred
        yield mf.found_new_remote(remote_snapshot)
        yield mf.when_idle()

        # status system should report our error
        self.assertThat(
            loads(carol.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Cancelled: some_file"
                                    )
                                }),
                            ),
                        }),
                    }),
                }),
            })
        )

    # XXX NOTE if this name gets longer, the resulting temp-paths can
    # become "too long" on windows causing failures
    @inline_callbacks
    def test_cancel1(self):
        """
        An update arrives but our attempt to update our Personal DMD is
        cancelled
        """

        parent_cap = random_immutable(directory=True)
        parent = RemoteSnapshot(
            relpath="foo",
            author=create_local_author("carol"),
            metadata={"modification_time": 0},
            capability=parent_cap,
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )

        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "a_file"

        # shortcut the download, always succeed
        def stream_capability(cap, filething):
            d = Deferred()
            d.callback(None)
            return d

        from twisted.internet import reactor

        carol = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "carol",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            tahoe_client=TahoeClientWrapper(
                stream_capability=stream_capability,
            ),
            start_folder_services=True,
        )
        self.addCleanup(carol.cleanup)

        service = carol.global_service.get_folder_service("default")

        # arrange to cancel the Personal DMD update
        service.file_factory._write_participant = wrap_frozen(
            service.file_factory._write_participant,
            update_snapshot=cancelled,
        )

        mf = service.file_factory.magic_file_for(magic_path.child(relpath))
        yield mf.found_new_remote(parent)
        yield mf.when_idle()

        # status system should report our error
        self.assertThat(
            loads(carol.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Cancelled: a_file"
                                    )
                                }),
                            ),
                        }),
                    }),
                }),
            })
        )


class AsyncFilesystemModificationTests(AsyncTestCase):
    """
    Tests for LocalMagicFolderFilesystem that are async
    """

    def setUp(self):
        super(AsyncFilesystemModificationTests, self).setUp()
        self.magic = FilePath(self.mktemp())
        self.magic.makedirs()
        self.staging = FilePath(self.mktemp())
        self.staging.makedirs()
        self.filesystem = LocalMagicFolderFilesystem(
            self.magic,
            self.staging,
        )

    @inline_callbacks
    def test_same_cap_different_file(self):
        """
        The stashed names are different for different relative-path names
        even if the capability is the same (because of convergent
        encryption, the capability may match if the content matches).
        """
        cap = random_immutable()

        class DummyClient:
            def stream_capability(self, cap, f):
                return succeed(None)

        stash_a = yield self.filesystem.download_content_to_staging("rel/a", cap, DummyClient())
        stash_b = yield self.filesystem.download_content_to_staging("rel/b", cap, DummyClient())
        self.assertThat(stash_a, NotEquals(stash_b))


class FilesystemModificationTests(SyncTestCase):
    """
    Tests for LocalMagicFolderFilesystem
    """

    def setUp(self):
        super(FilesystemModificationTests, self).setUp()
        self.magic = FilePath(self.mktemp())
        self.magic.makedirs()
        self.staging = FilePath(self.mktemp())
        self.staging.makedirs()
        self.filesystem = LocalMagicFolderFilesystem(
            self.magic,
            self.staging,
        )

    def test_delete(self):
        """
        Marking a file as deleted removes it
        """
        self.magic.child("foo").setContent(b"dummy")

        self.filesystem.mark_delete("foo")

        self.assertThat(
            self.magic.child("foo").exists(),
            Equals(False)
        )

    def test_delete_already_gone(self):
        """
        Error if the file is already gone
        """
        try:
            self.filesystem.mark_delete("foo")
        except Exception:
            # in python3, this will always be an OSError, but in
            # Python2 we get a WindowsError on windows and OSError on
            # other systems.
            pass
        else:
            raise AssertionError("Expected an exception")

    def test_overwrite_sub_dir(self):
        """
        Overwriting a non-existent directory creates the directory
        """
        dummy_content = b"dummy\n"
        staged = self.staging.child("new_content")
        staged.setContent(dummy_content)

        self.filesystem.mark_overwrite("sub/dir/foo", 12345, staged, None)

        self.assertThat(
            self.magic.child("sub").exists(),
            Equals(True)
        )
        self.assertThat(
            self.magic.child("sub").child("dir").exists(),
            Equals(True)
        )
        self.assertThat(
            self.magic.child("sub").child("dir").child("foo").getContent(),
            Equals(dummy_content)
        )

    def test_overwrite_sub_dir_is_file(self):
        """
        An incoming overwrite where a local file exists is an error
        """
        dummy_content = b"dummy\n"
        staged = self.staging.child("new_content")
        staged.setContent(dummy_content)
        # we put a _file_ in the way of the incoming directory
        with self.magic.child("sub").open("w") as f:
            f.write(b"pre-existing file")

        # for now, it's just an error if we find a file in a spot
        # where we wanted a directory .. perhaps there should be a
        # better / different answer?
        with ExpectedException(RuntimeError, ".*not a directory.*"):
            self.filesystem.mark_overwrite("sub/foo", 12345, staged, None)
        self.assertThat(
            self.magic.child("sub"),
            MatchesAll(
                AfterPreprocessing(
                    lambda f: f.isfile(),
                    Equals(True)
                ),
                AfterPreprocessing(
                    lambda f: f.getContent(),
                    Equals(b"pre-existing file")
                )
            )
        )
