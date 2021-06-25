from __future__ import (
    absolute_import,
    division,
    print_function,
)

from testtools.matchers import (
    ContainsDict,
    Contains,
    Not,
    AfterPreprocessing,
    Always,
)
from testtools.twistedsupport import (
    succeeded,
)

from hyperlink import (
    DecodedURL,
)

from twisted.python.filepath import (
    FilePath,
)

# After a Tahoe 1.15.0 or higher release, these should be imported
# from Tahoe instead
from magic_folder.testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)

from ..create import (
    magic_folder_create,
)
from ..config import (
    create_global_configuration,
)
from .fixtures import (
    NodeDirectory,
)
from .common import (
    SyncTestCase,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)


class TestAdd(SyncTestCase):
    """
    Test 'magic-folder add' command
    """

    def setUp(self):
        super(TestAdd, self).setUp()

        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com"),
            self.http_client,
        )
        self.magic_dir = FilePath(self.mktemp())
        self.magic_dir.makedirs()

        self.node = self.useFixture(NodeDirectory(FilePath(self.mktemp())))
        self.basedir = FilePath(self.mktemp())
        self.config = create_global_configuration(
            self.basedir,
            u"tcp:5555",
            self.node.path,
            u"tcp:localhost:5555",
        )

    def test_add_folder(self):
        d = magic_folder_create(
            self.config,
            u"test",
            u"alice",
            self.magic_dir,
            60,
            0,
            self.tahoe_client,
        )
        self.assertThat(d, succeeded(Always()))

        # confirm that we've added a magic-folder
        mf = self.config.get_magic_folder(u"test")

        # check the contents of the collective (should have alice's
        # read-only capability)
        collective_d = self.tahoe_client.directory_data(mf.collective_dircap)
        self.assertThat(collective_d, succeeded(Always()))

        metadata = collective_d.result
        # the collective should be a mutable directory and have "alice"
        # as a child pointing to a *read-only* directory.

        def extract_metadata(child_info):
            return child_info[1]  # ["dirnode", metadata]
        self.assertThat(
            metadata,
            ContainsDict({
                u"children": ContainsDict({
                    u"alice": AfterPreprocessing(
                        extract_metadata,
                        Not(Contains("rw_uri"))
                    )
                }),
            })
        )
