from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Tests relating generally to magic_folder.scanner
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
from ..scanner import (
    find_updated_files,
)


class FindUpdatesTests(AsyncTestCase):
    """
    Tests for ``find_updated_files``
    """

    def setUp(self):
        super(FindUpdatesTests, self).setUp()
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
            self.author,
            self.collective_cap,
            self.personal_cap,
            1,
        )

    def test_scan_new(self):
        """
        A completely new file is scanned
        """
        self.magic_path.child("new-file").setContent(b"dummy\n")

        files = []
        self.assertThat(
            find_updated_files(reactor, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([self.magic_path.child("new-file")]),
        )

    def test_scan_nothing(self):
        """
        An existing, non-updated file is not scanned
        """
        local = self.magic_path.child("new-file")
        local.setContent(b"dummy\n")
        snap = RemoteSnapshot(
            "new-file",
            self.author,
            metadata={
                "modification_time": int(local.getModificationTime()),
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[],
            content_cap="URI:CHK:",
        )
        self.config.store_remotesnapshot("new-file", snap)

        files = []
        self.assertThat(
            find_updated_files(reactor, self.config, files.append),
            succeeded(Always()),
        )
        self.assertThat(
            files,
            Equals([])
        )
