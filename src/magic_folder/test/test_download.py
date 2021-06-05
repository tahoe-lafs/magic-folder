from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Tests relating generally to magic_folder.downloader
"""

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
from twisted.python.filepath import (
    FilePath,
)
from ..config import (
    create_testing_configuration,
)
from ..downloader import (
    RemoteSnapshotCacheService,
)
from ..snapshot import (
    create_local_author,
    LocalSnapshot,
    sign_snapshot,
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
)
from .strategies import (
    tahoe_lafs_immutable_dir_capabilities,
)


class CacheTests(SyncTestCase):
    """
    Tests for ``RemoteSnapshotCacheService``
    """
    def setup_example(self):
        self.author = create_local_author(u"alice")
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
            120,
        )
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
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
                    name=Equals(u"foo"),
                    metadata=ContainsDict({
                        u"snapshot_version": Equals(1),
                        u"parents": Equals([]),
                        u"name": Equals(u"foo"),
                    }),
                )
            )
        )
