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

from json import (
    dumps,
)

from hyperlink import (
    DecodedURL,
)

from testtools.matchers import (
    MatchesStructure,
    Always,
    Equals,
    ContainsDict,
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
from ..config import (
    create_testing_configuration,
)
from ..downloader import (
    RemoteSnapshotCacheService,
)
from ..magic_folder import (
    MagicFolder,
)
from ..snapshot import (
    create_local_author,
    LocalSnapshot,
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
            FilePath(self.mktemp()),
            create_local_author("iris"),
            self.collective_cap,
            self.personal_cap,
            1,
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
            failed(self.cache.add_remote_capability(remote_cap)),
            Always(),
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

        snap = LocalSnapshot(
            mangled_name,
            self.author,
            metadata,
            FilePath("non-exist"),
            parents_local=[],
            parents_remote=[],
        )

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
                                            snap,
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
            self.cache.add_remote_capability(cap),
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


class UpdateTests(AsyncTestCase):
    """
    Tests for ``MagicFolderUpdaterService``

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
        self.stash_path = self.state_path.child("stash")
        self.stash_path.makedirs()

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
            FilePath(self.mktemp()),
            self.author,
            self.collective_cap,
            self.personal_cap,
            1,
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
            yield deferLater(reactor, 1.0)
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
            yield deferLater(reactor, 1.0)

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
            yield deferLater(reactor, 1.0)
        assert self.magic_path.child("foo").exists()
        assert self.magic_path.child("foo").getContent() == content, "content mismatch"

