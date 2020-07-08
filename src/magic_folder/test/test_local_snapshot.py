from __future__ import print_function

import os, sys, time
import stat, shutil, json
import mock
from os.path import join, exists, isdir
from errno import ENOENT

from twisted.internet import defer, task, reactor
from twisted.python.runtime import platform
from twisted.python.filepath import FilePath

from testtools.matchers import (
    Not,
    Is,
    ContainsDict,
    Equals,
)

from eliot import (
    Message,
    start_action,
    log_call,
)
from eliot.twisted import DeferredContext

from allmydata.interfaces import (
    IDirectoryNode,
    NoSharesError,
)
from allmydata.util.assertutil import precondition

from allmydata.util import fileutil, configutil, yamlutil
from allmydata.util.encodingutil import get_filesystem_encoding, to_filepath
from allmydata.util.consumer import download_to_data

from allmydata.util.fileutil import get_pathinfo
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.immutable.upload import Data
from allmydata.mutable.common import (
        UnrecoverableFileError,
)

from eliot.twisted import (
    inline_callbacks,
)

from magic_folder.util.eliotutil import (
    log_call_deferred,
)

from magic_folder.magic_folder import (
    MagicFolder,
    WriteFileMixin,
    ConfigurationError,
    get_inotify_module,
    load_magic_folders,
    maybe_upgrade_magic_folders,
    is_new_file,
    _upgrade_magic_folder_config,
)
from ..util import (
    fake_inotify,
)

from .. import (
    magicfolderdb,
    magicpath,
)

from .no_network import GridTestMixin
from .common_util import ReallyEqualMixin
from .common import (
    ShouldFailMixin,
    SyncTestCase,
    AsyncTestCase,
    skipIf,
)
from .cli.test_magic_folder import MagicFolderCLITestMixin


@attr.s
class MemoryMagicFolderDatabase(object):

    snapshots = attr.ib(default=attr.Factory(dict))

    def store_local_snapshot(self, snapshot):
        self.snapshots[snapshot.name] = (
            snapshot.metadata,
            snapshot.content_path,
            snapshot.parents_local,
        )

    def get_local_snapshot(self, name, author):
        meta, content, parents = self.snapshots[name]  # may raise
        return LocalSnapshot(
            name=name,
            author=author,
            metdata=meta,
            content_path=content,
            parents_local=parents,
        )


class LocalSnapshotTests(AsyncTestCase):

    def setUp(self):
        self.db = MemoryMagicFolderDatabase()
        self.clock = Clock()
        self.author = create_local_author("alice")
        self.stash_dir = FilePath("/tmp")  # XXX FIXME
        self.magic_path = FilePath("/tmp")  # XXX FIXME
        self.snapshot_creator = LocalSnapshotCreator(
            magic_path=self.magic_path,
            db=self.db,
            clock=self.clock,
            author=self.author,
            stash_dir=self.stash_dir,
        )

    def test_add_single_file(self):
        foo = self.magic_path.child("foo")
        with open(foo, "w") as f:
            f.write("fake data\n" * 100)  # hypothesis, probably

        self.snapshot_creator.add_files(foo)
        self.startService()
        # I *think* we should have processed 1 item by now..
        self.assertThat(
            self.db,
            MatchesStructure(
                snapshots=Equals([
                    LocalSnapshot(
                        name="foo",
                        author=self.author,
                        metadata={},
                        content_path="some random content path",
                        parents_local=[],
                    )
                ])
            )
        )
