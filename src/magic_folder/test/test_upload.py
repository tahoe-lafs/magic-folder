from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

from json import (
    dumps,
    loads,
)

from re import (
    escape,
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
from twisted.internet.defer import (
    succeed,
)
from twisted.web.resource import (
    ErrorPage,
)
from ..snapshot import (
    create_local_author,
    RemoteSnapshot,
)
from ..magicpath import (
    path2magic,
)
from ..util.capabilities import (
    is_immutable_directory_cap,
    to_verify_capability,
)
from ..util.file import (
    get_pathinfo,
)

from .common import (
    SyncTestCase,
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


class UploadTests(SyncTestCase):
    """
    Tests for upload cases
    """

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
        author = create_local_author("alice")
        f = self.useFixture(MagicFileFactoryFixture(
            temp=FilePath(self.mktemp()),
            author=author,
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
        local_path_bytes = local_path.asBytesMode("utf8")
        if not local_path_bytes.parent().exists():
            local_path_bytes.parent().makedirs()
        with local_path_bytes.open("w") as local_file:
            local_file.write("foo\n" * 20)
        mf = f.magic_file_factory.magic_file_for(local_path)
        self.assertThat(
            mf.create_update(),
            succeeded(Always()),
        )
        self.assertThat(
            mf.when_idle(),
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

        author = create_local_author("alice")
        f = self.useFixture(MagicFileFactoryFixture(
            temp=FilePath(self.mktemp()),
            author=author,
            root=broken_root,
            upload_dircap=upload_dircap,
        ))
        local_path = f.config.magic_path.child(relpath)
        mf = f.magic_file_factory.magic_file_for(local_path)

        retries = []
        snapshots = []

        def retry(*args, **kw):
            retries.append((args, kw))
            return succeed("synchronous, no retry")
        mf._delay_later = retry

        for content in contents:
            # data = io.BytesIO(content)
            with local_path.asBytesMode("utf8").open("w") as local_file:
                local_file.write(content)
            d = mf.create_update()
            d.addCallback(snapshots.append)
            self.assertThat(
                d,
                succeeded(Always()),
            )

        self.eliot_logger.flushTracebacks(TahoeAPIError)

        local_snapshot = snapshots[-1]
        self.assertEqual(
            local_snapshot,
            f.config.get_local_snapshot(relpath),
        )
        self.assertThat(
            local_snapshot.content_path.getContent(),
            Equals(content),
        )
        self.assertThat(
            len(retries),
            Equals(1),
        )


class MagicFileFactoryTests(SyncTestCase):
    """
    Test aspects of MagicFileFactory
    """

    @given(
        tahoe_lafs_dir_capabilities(),
    )
    def test_existing_conflict(self, upload_dircap):
        """
        An already-conflicted file shows up that way upon startup.
        """
        relpath = "conflicted_relpath"
        author = create_local_author("alice")

        f = self.useFixture(MagicFileFactoryFixture(
            temp=FilePath(self.mktemp()),
            author=author,
            upload_dircap=upload_dircap,
        ))
        config = f.config

        local = f.magic_path.child(relpath)
        with local.asBytesMode("utf8").open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        snap = RemoteSnapshot(
            relpath,
            author,
            metadata={
                "modification_time": int(
                    local.asBytesMode("utf-8").getModificationTime()
                ),
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[],
            content_cap="URI:CHK:",
            metadata_cap="URI:CHK:",
        )
        config.store_downloaded_snapshot(
            relpath, snap, get_pathinfo(local).state
        )

        # mark it as a conflict
        config.add_conflict(snap)

        # create a MagicFile file for this relpath now
        mf = f.magic_file_factory.magic_file_for(local)

        # we can't know the current state, but we can see what it does
        transitions = []

        def trace(*args):
            transitions.append(args)
        mf.set_trace(trace)

        # send in a remote update; if we were already conflicted it'll
        # loop into that state and stay conflicted .. otherwise it'll
        # try to upload
        child = RemoteSnapshot(
            relpath,
            author,
            metadata={
                "modification_time": int(1234),
            },
            capability="URI:DIR2-CHK:",
            parents_raw=[snap.capability],
            content_cap="URI:CHK:",
            metadata_cap="URI:CHK:",
        )
        mf.found_new_remote(child)

        self.assertThat(
            transitions,
            Equals([
                ('_conflicted', '_remote_update', '_conflicted'),
            ])
        )
