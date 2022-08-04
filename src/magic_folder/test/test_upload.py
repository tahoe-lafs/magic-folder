from json import (
    dumps,
    loads,
)

from re import (
    escape,
)

from .matchers import (
    matches_flushed_traceback,
)
from testtools.matchers import (
    AfterPreprocessing,
    MatchesListwise,
    ContainsDict,
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

from eliot.twisted import (
    inline_callbacks,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    Deferred,
    DeferredList,
    CancelledError,
)
from twisted.web.resource import (
    ErrorPage,
)
from hyperlink import (
    DecodedURL,
)

from ..snapshot import (
    create_local_author,
    RemoteSnapshot,
)
from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from ..tahoe_client import (
    create_tahoe_client,
)
from ..magicpath import (
    path2magic,
)
from ..util.file import (
    get_pathinfo,
)
from ..util.wrap import (
    wrap_frozen,
)
from ..util.twisted import (
    cancelled,
)
from ..util.capabilities import (
    random_immutable,
)

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
    MagicFolderNode,
    TahoeClientWrapper,
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
        f.root._uri.data[upload_dircap.danger_real_capability_string()] = dumps([
            u"dirnode",
            {u"children": {}},
        ])

        # create a local snapshot
        local_path = f.config.magic_path.preauthChild(relpath)
        if not local_path.parent().exists():
            local_path.parent().makedirs()
        with local_path.open("w") as local_file:
            local_file.write(b"foo\n" * 20)
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
            loads(f.root._uri.data[upload_dircap.danger_real_capability_string()])[1][u"children"],
            Equals({
                path2magic(relpath): [
                    u"dirnode", {
                        u"ro_uri": remote_snapshot_cap.danger_real_capability_string(),
                        u"verify_uri": remote_snapshot_cap.to_verifier().danger_real_capability_string(),
                        u"mutable": False,
                        u"format": u"CHK",
                    },
                ],
            }),
        )

        # test whether we got a capability
        self.assertThat(
            remote_snapshot_cap.is_immutable_directory(),
            Equals(True)
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
            d = Deferred()
            retries.append((d, (args, kw)))
            return d
        mf._delay_later = retry

        for content in contents:
            with local_path.open("w") as local_file:
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
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        snap = RemoteSnapshot(
            relpath,
            author,
            metadata={
                "modification_time": int(
                    local.getModificationTime()
                ),
            },
            capability=random_immutable(directory=True),
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
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
            capability=random_immutable(directory=True),
            parents_raw=[snap.capability.danger_real_capability_string()],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )
        mf.found_new_remote(child)

        self.assertThat(
            transitions,
            Equals([
                ('_conflicted', '_remote_update', '_conflicted'),
            ])
        )


class AsyncMagicFileTests(AsyncTestCase):
    """
    MagicFile tests requiring the reactor
    """

    @inline_callbacks
    def test_local_queue(self):
        """
        Queuing up two updates 'at once' causes two versions to be
        produced
        """
        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "a_local_file"

        # we provide our own Tahoe client here so that we can control
        # when uploads etc complete .. so that we can queue up a
        # couple uploads without having the first complete
        # "immediately"
        tahoe_root = create_fake_tahoe_root()
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(tahoe_root),
        )

        from twisted.internet import reactor

        alice = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "alice",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            tahoe_client=tahoe_client,
            # note: we do start services, but later..
            start_folder_services=False,
        )
        self.addCleanup(alice.cleanup)

        config = alice.global_config.get_magic_folder("default")
        service = alice.global_service.get_folder_service("default")

        # because we provided a tahoe_root to MagicFolderNode, it
        # doesn't put the collective/personal mutable DMDs in to the
        # data-store...and so we also need to delay startup of
        # services until we've done this, so that the scanner may find
        # things.

        # Collective DMD
        tahoe_root._uri.data[config.collective_dircap.danger_real_capability_string()] = dumps([
            "dirnode",
            {
                "children": {
                    "alice": [
                        "dirnode",
                        {
                            "mutable": True,
                            "ro_uri": config.upload_dircap.to_readonly().danger_real_capability_string(),
                            "verify_uri": config.upload_dircap.to_verifier().danger_real_capability_string(),
                            "format": "SDMF",
                        },
                    ],
                }
            }
        ])

        # Personal DMD
        tahoe_root._uri.data[config.upload_dircap.danger_real_capability_string()] = dumps([
            "dirnode",
            {
                "children": {},
            }
        ])

        # all data available, we can start services
        service.startService()

        local = magic_path.child(relpath)
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        mf = service.file_factory.magic_file_for(local)
        updates = []
        updates.append(mf.create_update())
        updates.append(mf.create_update())

        # we wait for the snapshots to be created
        snapshots = yield DeferredList(updates)

        # we also want the remotes to be actually-uploaded so we can
        # check their structure
        yield mf.when_idle()

        # one snapshot should have no parents, the other should have
        # the first as its parent .. DeferredList returns 2-tuples
        # though
        self.assertThat(
            all(
                ok
                for ok, snap
                in snapshots
            ),
            Equals(True)
        )
        snap0, snap1 = (snap for ok, snap in snapshots)
        self.assertThat(snap0.remote_snapshot.parents_raw, Equals([]))
        self.assertThat(snap0.parents_local, Equals([]))
        self.assertThat(snap0.parents_remote, Equals([]))

        self.assertThat(snap1.remote_snapshot.parents_raw, Equals([snap0.remote_snapshot.capability.danger_real_capability_string()]))
        self.assertThat(snap1.parents_local, Equals([]))
        self.assertThat(snap1.parents_remote, Equals([snap0.remote_snapshot.capability]))

    @inline_callbacks
    def test_fail_upload_dmd_update(self):
        """
        While uploading a local snapshot we fail to update our Personal
        DMD. A retry is attempted.
        """
        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "random_local_file"

        # we provide our own Tahoe client here so that we can control
        # when uploads etc complete .. so that we can queue up a
        # couple uploads without having the first complete
        # "immediately"
        tahoe_root = create_fake_tahoe_root()
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://invalid./"),
            create_tahoe_treq_client(tahoe_root),
        )

        from twisted.internet import reactor

        alice = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "alice",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            tahoe_client=tahoe_client,
            # note: we do start services, but later..
            start_folder_services=False,
        )
        self.addCleanup(alice.cleanup)

        config = alice.global_config.get_magic_folder("default")
        service = alice.global_service.get_folder_service("default")

        # because we provided a tahoe_root to MagicFolderNode, it
        # doesn't put the collective/personal mutable DMDs in to the
        # data-store...and so we also need to delay startup of
        # services until we've done this, so that the scanner may find
        # things.

        # Collective DMD
        tahoe_root._uri.data[config.collective_dircap.danger_real_capability_string()] = dumps([
            "dirnode",
            {
                "children": {
                    "alice": [
                        "dirnode",
                        {
                            "mutable": True,
                            "ro_uri": config.upload_dircap.to_readonly().danger_real_capability_string(),
                            "verify_uri": config.upload_dircap.to_verifier().danger_real_capability_string(),
                            "format": "SDMF",
                        },
                    ],
                }
            }
        ]).encode("utf8")

        # Personal DMD
        tahoe_root._uri.data[config.upload_dircap.danger_real_capability_string()] = dumps([
            "dirnode",
            {
                "children": {},
            }
        ]).encode("utf8")

        # all data available, we can start services
        service.startService()

        local = magic_path.child(relpath)
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        mf = service.file_factory.magic_file_for(local)

        # arrange to fail the Personal DMD update that will result
        # while uploading this update
        tahoe_root.fail_next_directory_update()
        yield mf.create_update()
        yield mf.when_idle()

        # status system should report our error
        self.assertThat(
            loads(alice.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Error updating personal DMD: Couldn't add random_local_file to directory. Error code 500"
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
                matches_flushed_traceback(Exception, "Couldn't add random_local_file to directory. Error code 500")
            ]),
        )

    @inline_callbacks
    def test_cancel_upload(self):
        """
        While an upload is ongoing it is cancelled
        """

        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "random_local_file"

        from twisted.internet import reactor

        alice = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "alice",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            tahoe_client=TahoeClientWrapper(
                create_immutable=cancelled,
            ),
            # note: we do start services, but later..
            start_folder_services=True,
        )
        self.addCleanup(alice.cleanup)

        service = alice.global_service.get_folder_service("default")

        local = magic_path.child(relpath)
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        mf = service.file_factory.magic_file_for(local)
        yield mf.create_update()
        yield mf.when_idle()

        # status system should report our error
        self.assertThat(
            loads(alice.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Cancelled: random_local_file"
                                    )
                                }),
                            ),
                        }),
                    }),
                }),
            })
        )
        self.assertThat(
            self.eliot_logger.flush_tracebacks(CancelledError),
            MatchesListwise([
                matches_flushed_traceback(CancelledError),
            ]),
        )

    @inline_callbacks
    def test_cancel_dmd(self):
        """
        An attempt to update the Personal DMD after an upload is
        cancelled.
        """

        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "a_local_file"

        from twisted.internet import reactor

        alice = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "alice",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            start_folder_services=True,
        )
        self.addCleanup(alice.cleanup)

        service = alice.global_service.get_folder_service("default")
        local = magic_path.child(relpath)
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        # arrange to fail the personal-dmd update
        service.file_factory._write_participant = wrap_frozen(
            service.file_factory._write_participant,
            update_snapshot=cancelled,
        )

        # simulate the update
        mf = service.file_factory.magic_file_for(local)
        yield mf.create_update()
        yield mf.when_idle()

        # status system should report our error
        self.assertThat(
            loads(alice.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: errors[0],
                                ContainsDict({
                                    "summary": Equals(
                                        "Cancelled: a_local_file"
                                    )
                                }),
                            ),
                        }),
                    }),
                }),
            })
        )

    @inline_callbacks
    def test_update_dmd_fails_then_succeeds(self):
        """
        Some attempts to update the Personal DMD fail, then stop failing.
        """

        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "trouble"

        from twisted.internet import reactor

        alice = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "alice",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            start_folder_services=True,
        )
        self.addCleanup(alice.cleanup)

        service = alice.global_service.get_folder_service("default")
        local = magic_path.child(relpath)
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        # arrange for the "participant.update_snapshot" call to fail
        # exactly twice and then succeed
        bad_stuff = Exception("bad stuff")

        def temporary_error(orig, count):
            temporary_error.remain = count

            def maybe_fail(*args, **kw):
                temporary_error.remain -= 1
                if temporary_error.remain >= 0:
                    raise bad_stuff
                return orig(*args, **kw)
            return maybe_fail

        service.file_factory._write_participant = wrap_frozen(
            service.file_factory._write_participant,
            update_snapshot=temporary_error(
                service.file_factory._write_participant.update_snapshot,
                2,
            )
        )

        # simulate the update
        mf = service.file_factory.magic_file_for(local)
        yield mf.create_update()
        yield mf.when_idle()

        # status system should report our error
        self.assertThat(
            loads(alice.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: [error["summary"] for error in errors],
                                Equals([
                                    "Error updating personal DMD: bad stuff",
                                    "Error updating personal DMD: bad stuff",
                                ]),
                            ),
                        }),
                    }),
                }),
            })
        )
        # ...as should the logger
        self.assertThat(
            self.eliot_logger.flush_tracebacks(Exception),
            AfterPreprocessing(
                lambda errors: [err["reason"] for err in errors],
                Equals([
                    bad_stuff,
                    bad_stuff,
                ])
            )
        )

    @inline_callbacks
    def test_upload_fails_then_succeeds(self):
        """
        Some attempts to upload fail, then stop failing.
        """

        magic_path = FilePath(self.mktemp())
        magic_path.makedirs()
        relpath = "danger"

        from twisted.internet import reactor

        alice = MagicFolderNode.create(
            reactor=reactor,
            basedir=FilePath(self.mktemp()),
            folders={
                "default": {
                    "magic-path": magic_path,
                    "author-name": "alice",
                    "admin": True,
                    "poll-interval": 100,
                    "scan-interval": 100,
                }
            },
            start_folder_services=True,
        )
        self.addCleanup(alice.cleanup)

        service = alice.global_service.get_folder_service("default")

        # arrange for the "tahoe_client.create_immutable" call to fail
        # exactly twice and then succeed
        bad_stuff = Exception("bad stuff")

        def temporary_error(orig, count):
            temporary_error.remain = count

            def maybe_fail(*args, **kw):
                temporary_error.remain -= 1
                if temporary_error.remain >= 0:
                    d = Deferred()
                    d.errback(bad_stuff)
                    return d
                return orig(*args, **kw)
            return maybe_fail

        service.file_factory._uploader = service.uploader_service = wrap_frozen(
            service.uploader_service,
            upload_snapshot=temporary_error(
                service.uploader_service.upload_snapshot,
                2,
            )
        )

        local = magic_path.child(relpath)
        with local.open("w") as local_f:
            local_f.write(b"dummy\n" * 50)

        # simulate the update
        mf = service.file_factory.magic_file_for(local)
        yield mf.create_update()
        yield mf.when_idle()

        # status system should report our error
        self.assertThat(
            loads(alice.global_service.status_service._marshal_state()),
            ContainsDict({
                "state": ContainsDict({
                    "folders": ContainsDict({
                        "default": ContainsDict({
                            "errors": AfterPreprocessing(
                                lambda errors: [error["summary"] for error in errors],
                                Equals([
                                    "Error uploading danger: bad stuff",
                                    "Error uploading danger: bad stuff",
                                ]),
                            ),
                        }),
                    }),
                }),
            })
        )
        # ...as should the logger
        # XXX why does this only return _one_ error -- should be two!
        self.assertThat(
            self.eliot_logger.flush_tracebacks(Exception),
            AfterPreprocessing(
                lambda errors: [err["reason"] for err in errors],
                Equals([
                    bad_stuff,
                ])
            )
        )
