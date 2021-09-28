from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import io

from json import (
    dumps,
    loads,
)

import attr

from re import (
    escape,
)

from zope.interface import (
    implementer,
)

from testtools.matchers import (
    MatchesPredicate,
    Always,
    Equals,
)
from testtools import (
    ExpectedException,
)
from testtools.twistedsupport import (
    succeeded,
)
from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
    lists,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.resource import (
    ErrorPage,
)
from ..snapshot import (
    create_local_author,
    create_snapshot,
)
from ..magicpath import (
    path2magic,
)
from ..util.capabilities import is_immutable_directory_cap, to_verify_capability
from ..util.file import PathState
from twisted.internet import task

from .common import (
    SyncTestCase,
    AsyncTestCase,
)
from .strategies import (
    path_segments,
    relative_paths,
    tahoe_lafs_dir_capabilities,
)

from .fixtures import (
    MagicFileFactoryFixture,
)

from magic_folder.tahoe_client import (
    TahoeAPIError,
)


class UploadTests(AsyncTestCase):
    """
    Tests for upload cases
    """
    def setUp(self):
        super(UploadTests, self).setUp()
        self.author = create_local_author(u"alice")

    @given(
        relpath=relative_paths(),
        content=binary(),
        upload_dircap=tahoe_lafs_dir_capabilities(),
    )
    def test_commit_a_file(self, relpath, content, upload_dircap):
        """
        Add a file into localsnapshot store, start the service which
        should result in a remotesnapshot corresponding to the
        localsnapshot.
        """
        f = self.useFixture(MagicFileFactoryFixture(
            temp=FilePath(self.mktemp()),
            author=self.author,
            upload_dircap=upload_dircap,
        ))
        config = f.config

        # Make the upload dircap refer to a dirnode so the snapshot creator
        # can link files into it.
        f.root._uri.data[upload_dircap] = dumps([
            u"dirnode",
            {u"children": {}},
        ])

        # create a local snapshot
        local_path = f.config.magic_path.preauthChild(relpath)
        if not local_path.parent().exists():
            local_path.parent().makedirs()
        with local_path.open("w") as local_file:
            local_file.write("foo\n" * 20)
        mf = f.magic_file_factory.magic_file_for(local_path)
        d = mf.create_update()
        self.assertThat(
            d,
            succeeded(Always()),
        )
        d = mf.when_idle()
        self.assertThat(
            d,
            succeeded(Always()),
        )

        remote_snapshot_cap = config.get_remotesnapshot(relpath)

        # Verify that the new snapshot was linked in to our upload directory.
        self.assertThat(
            loads(f.root._uri.data[upload_dircap])[1][u"children"],
            Equals({
                path2magic(relpath): [
                    u"dirnode", {
                        u"ro_uri": remote_snapshot_cap.decode("utf-8"),
                        u"verify_uri": to_verify_capability(remote_snapshot_cap),
                        u"mutable": False,
                        u"format": u"CHK",
                    },
                ],
            }),
        )

        # test whether we got a capability
        self.assertThat(
            remote_snapshot_cap,
            MatchesPredicate(
                is_immutable_directory_cap,
                "%r is not a immuutable directory Tahoe-LAFS URI",
            ),
        )

        with ExpectedException(KeyError, escape(repr(relpath))):
            config.get_local_snapshot(relpath)

    @given(
        path_segments(),
        lists(
            binary(),
            min_size=1,
            max_size=2,
        ),
        tahoe_lafs_dir_capabilities(),
    )
    def test_write_snapshot_to_tahoe_fails(self, relpath, contents, upload_dircap):
        """
        If any part of a snapshot upload fails then the metadata for that snapshot
        is retained in the local database and the snapshot content is retained
        in the stash.
        """
        broken_root = ErrorPage(500, "It's broken.", "It's broken.")

        f = self.useFixture(MagicFileFactoryFixture(
            temp=FilePath(self.mktemp()),
            author=self.author,
            root=broken_root,
            upload_dircap=upload_dircap,
        ))
        local_path = f.config.magic_path.child(relpath)
        mf = f.magic_file_factory.magic_file_for(local_path)
        snapshots = []

        for content in contents:
            # data = io.BytesIO(content)
            with local_path.open("w") as local_file:
                local_file.write(content)
                d = mf.create_update()
                d.addCallback(snapshots.append)
                self.assertThat(
                    d,
                    succeeded(Always()),
                )

        self.assertThat(
            mf.when_idle(),
            succeeded(Always()),
        )
        self.eliot_logger.flushTracebacks(TahoeAPIError)

        local_snapshot = snapshots[-1]
        print("ZZZZZ", local_snapshot, f.config.get_local_snapshot(relpath))
        self.assertEqual(
            local_snapshot,
            f.config.get_local_snapshot(relpath),
        )
        self.assertThat(
            local_snapshot.content_path.getContent(),
            Equals(content),
        )
