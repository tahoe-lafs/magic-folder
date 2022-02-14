import sqlite3
import itertools
from io import (
    BytesIO,
)
from re import (
    escape,
)
from functools import (
    partial,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    Cooperator,
)

from hypothesis import (
    given,
    assume,
    example,
)
from hypothesis.strategies import (
    one_of,
    just,
    binary,
    lists,
    tuples,
    text,
    characters
)

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    Equals,
    NotEquals,
    Contains,
    MatchesStructure,
    Always,
    HasLength,
    AfterPreprocessing,
)
from testtools.twistedsupport import (
    succeeded,
)

from hyperlink import (
    DecodedURL,
    URL,
)

from .common import (
    SyncTestCase,
)
from .fixtures import (
    NodeDirectory,
)
from .strategies import (
    relative_paths,
    path_segments,
    path_segments_without_dotfiles,
    port_numbers,
    interfaces,
    magic_folder_filenames,
    remote_snapshots,
    local_snapshots,
    folder_names,
    path_states,
    author_names,
    tahoe_lafs_immutable_dir_capabilities,
    tahoe_lafs_chk_capabilities,
)
from ..common import (
    APIError,
    InvalidMagicFolderName,
    NoSuchMagicFolder,
)
from ..config import (
    Conflict,
    LocalSnapshotMissingParent,
    RemoteSnapshotWithoutPathState,
    SQLite3DatabaseLocation,
    MagicFolderConfig,
    endpoint_description_to_http_api_root,
    create_global_configuration,
    load_global_configuration,
)
from ..snapshot import (
    create_local_author,
    create_snapshot,
    RemoteSnapshot,
    LocalSnapshot,
)
from ..util.capabilities import (
    Capability,
    random_dircap,
    random_immutable,
)
from ..util.file import (
    PathState,
    seconds_to_ns,
)


class TestGlobalConfig(SyncTestCase):

    def setUp(self):
        super(TestGlobalConfig, self).setUp()
        self.setup_tempdir()

    def setup_example(self):
        self.setup_tempdir()

    def setup_tempdir(self):
        self.temp = FilePath(self.mktemp())
        self.node_dir = FilePath(self.mktemp())
        self.tahoe_dir = self.useFixture(NodeDirectory(self.node_dir))

    @given(
        path_segments_without_dotfiles(),
    )
    def test_create(self, dirname):
        """
        ``create_global_configuration`` accepts a path that doesn't exist to which
        to write the configuration.
        """
        confdir = self.temp.child(dirname)
        config = create_global_configuration(confdir, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        self.assertThat(
            config,
            MatchesStructure(
                api_endpoint=Equals(u"tcp:1234"),
            ),
        )

    def test_create_existing_dir(self):
        """
        ``create_global_configuration`` raises ``ValueError`` if the configuration
        path passed to it already exists.
        """
        self.temp.makedirs()
        with ExpectedException(ValueError, ".*{}.*".format(escape(self.temp.path))):
            create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")

    def test_load_db(self):
        """
        ``load_global_configuration`` can read the global configuration written by
        ``create_global_configuration``.
        """
        create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        config = load_global_configuration(self.temp)
        self.assertThat(
            config,
            MatchesStructure(
                api_endpoint=Equals(u"tcp:1234"),
                tahoe_client_url=Equals(DecodedURL.from_text(u"http://127.0.0.1:9876/")),
            )
        )

    def test_load_db_no_such_directory(self):
        """
        ``load_global_configuration`` raises ``ValueError`` if passed a path which
        does not exist.
        """
        non_dir = self.temp.child("non-existent")
        with ExpectedException(ValueError, ".*{}.*".format(escape(non_dir.path))):
            load_global_configuration(non_dir)

    def test_rotate_api_key(self):
        """
        ``GlobalConfigDatabase.rotate_api_token`` replaces the current API token
        with a new one.
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        pre = config.api_token
        config.rotate_api_token()
        self.assertThat(
            config.api_token,
            NotEquals(pre)
        )

    def test_change_api_endpoint(self):
        """
        An assignment that changes the value of
        ``GlobalConfigDatabase.api_endpoint`` results in the new value being
        available when the database is loaded again with
        ``load_global_configuration``.
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        config.api_endpoint = "tcp:42"
        config2 = load_global_configuration(self.temp)
        self.assertThat(
            config2.api_endpoint,
            Equals(config.api_endpoint)
        )
        self.assertThat(
            config2.api_endpoint,
            Equals("tcp:42")
        )


class EndpointDescriptionConverterTests(SyncTestCase):
    """
    Tests for ``endpoint_description_to_http_api_root``.
    """
    @given(port_numbers(), one_of(just(None), interfaces()))
    def test_tcp(self, port_number, interface):
        """
        A TCP endpoint can be converted to an **http** URL.
        """
        return self._tcpish_test(u"tcp", u"http", port_number, interface)

    @given(port_numbers(), one_of(just(None), interfaces()))
    def test_ssl(self, port_number, interface):
        """
        An SSL endpoint can be converted to an **https** URL.
        """
        return self._tcpish_test(u"ssl", u"https", port_number, interface)

    def _tcpish_test(self, endpoint_type, url_scheme, port_number, interface):
        """
        Assert that a sufficiently TCP-like endpoint string can be parsed into an
        HTTP or HTTPS URL.
        """
        endpoint = u"{}:{}{}".format(
            endpoint_type,
            port_number,
            u"" if interface is None else u":interface={}".format(interface),
        )
        self.assertThat(
            endpoint_description_to_http_api_root(endpoint),
            Equals(
                URL(
                    scheme=url_scheme,
                    host=u"127.0.0.1" if interface in (None, u"0.0.0.0") else interface,
                    port=port_number,
                ).get_decoded_url(),
            ),
        )


class GlobalConfigDatabaseMagicFolderTests(SyncTestCase):
    """
    Tests for the ``GlobalConfigDatabase`` APIs that deal with individual
    ``MagicFolderConfig`` instances.
    """
    def setUp(self):
        super(GlobalConfigDatabaseMagicFolderTests, self).setUp()
        self.setup_tempdir()

    def setup_example(self):
        self.setup_tempdir()

    def setup_tempdir(self):
        self.temp = FilePath(self.mktemp())
        self.node_dir = FilePath(self.mktemp())
        self.tahoe_dir = self.useFixture(NodeDirectory(self.node_dir))


    @given(
        folder_names(),
    )
    # These examples ensure that it is possible to generate magic folders that
    # contain characters that are invalid on windows.
    @example(u".")
    @example(u":")
    @example(u'"')
    def test_create_folder(self, folder_name):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        magic_folder = config.create_magic_folder(
            folder_name,
            magic,
            alice,
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            60,
            60,
        )
        self.assertThat(
            magic_folder.author,
            Equals(alice),
        )

    def test_create_folder_duplicate(self):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        config.create_magic_folder(
            u"foo",
            magic,
            alice,
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            60,
            60,
        )
        with ExpectedException(APIError, "Already have a magic-folder named 'foo'"):
            config.create_magic_folder(
                u"foo",
                magic,
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
                60,
            )

    def test_create_folder_trailing_dot_space(self):
        """
        We can create folders that differ only in having a trailing dot or space in the name.

        Windows will strip a trailing dot or space from filenames, so test that
        we don't get state-directory colisions with names that differ only in a
        trailing dot or space.
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        config.create_magic_folder(
            u"foo",
            magic,
            alice,
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            60,
            60,
        )
        config.create_magic_folder(
            u"foo.",
            magic,
            alice,
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            60,
            60,
        )
        config.create_magic_folder(
            u"foo ",
            magic,
            alice,
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            60,
            60,
        )

    def test_folder_nonexistant_magic_path(self):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        with ExpectedException(APIError, ".*{}.*".format(escape(magic.path))):
            config.create_magic_folder(
                u"foo",
                magic,
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
                None,
            )

    def test_folder_state_already_exists(self):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        name = u"foo"
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        state = config._get_state_path(name)
        magic.makedirs()
        state.makedirs()  # shouldn't pre-exist, though
        with ExpectedException(APIError, ".*{}.*".format(escape(state.path))):
            config.create_magic_folder(
                name,
                state,
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
                60,
            )

    def test_folder_get_path(self):
        """
        we can retrieve the stash-path from a magic-folder-confgi
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        name = u"foo"
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        config.create_magic_folder(
            name,
            magic,
            alice,
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            60,
            60,
        )
        self.assertThat(config.list_magic_folders(), Contains(u"foo"))
        mf_config = config.get_magic_folder(u"foo")
        self.assertThat(
            mf_config.stash_path,
            Equals(config._get_state_path(name).child(u"stash")),
        )

    def test_folder_cache(self):
        """
        After calling `remove_magic_folder`, `get_magic_folder` raises `NoSuchMagicFolder`
        even if there is a live reference to the previous `MagicFolderConfig` instance.
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        name = u"foo"
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        config.create_magic_folder(
            name,
            magic,
            alice,
            Capability.from_string(u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq"),
            Capability.from_string(u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia"),
            60,
            None,
        )

        # We grab a reference to the `MagicFolderConfig` so that the cache
        # doesn't get cleaned up by the object being collected. This simulates
        # the case of MagicFolder having circular references and pointers to
        # the MagicFolderConfig.
        folder_config = config.get_magic_folder(name)

        config.remove_magic_folder(name)
        with self.assertRaises(NoSuchMagicFolder):
            config.get_magic_folder(name)

        del folder_config

    def test_get_folder_nonexistent(self):
        """
        an error to retrieve a non-existent folder
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        with ExpectedException(NoSuchMagicFolder):
            config.get_magic_folder(u"non-existent")

    @given(
        tuples(
            text(),
            characters(
                whitelist_categories=(
                    "Cc", "Cs", "Cn",
                ),
                whitelist_characters=("/", "\\"),
            ),
            text(),
        ).map("".join)
    )
    def test_get_folder_illegal_characters(self, folder_name):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        alice = create_local_author(u"alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        with ExpectedException(InvalidMagicFolderName):
            config.create_magic_folder(
                folder_name,
                magic,
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
                60,
            )


class StoreLocalSnapshotTests(SyncTestCase):
    """
    Tests for the ``MagicFolderConfig`` APIs which store and load
    ``LocalSnapshot`` objects.
    """
    def setUp(self):
        super(StoreLocalSnapshotTests, self).setUp()
        self.author = create_local_author(u"alice")
        self.uncooperator = Cooperator(
            terminationPredicateFactory=lambda: lambda: False,
            scheduler=lambda f: f(),
        )
        self.addCleanup(self.uncooperator.stop)

    def setup_example(self):
        self.temp = FilePath(self.mktemp())
        self.stash = self.temp.child("stash")
        self.stash.makedirs()
        self.magic = self.temp.child("magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            # collective dircap
            random_dircap(readonly=True),
            # upload dircap
            random_dircap(),
            self.magic,
            60,
            60,
        )

    @given(
        content1=binary(min_size=1),
        content2=binary(min_size=1),
        filename=magic_folder_filenames(),
        stash_subdir=path_segments(),
    )
    def test_serialize_store_deserialize_snapshot(self, content1, content2, filename, stash_subdir):
        """
        create a new snapshot (this will have no parent snapshots).
        """
        data1 = BytesIO(content1)

        snapshots = []

        d = create_snapshot(
            relpath=filename,
            author=self.author,
            data_producer=data1,
            snapshot_stash_dir=self.stash,
            parents=[],
            cooperator=self.uncooperator,
        )
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        self.db.store_local_snapshot(
            snapshots[0],
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )

        # now modify the same file and create a new local snapshot
        data2 = BytesIO(content2)
        d = create_snapshot(
            relpath=filename,
            author=self.author,
            data_producer=data2,
            snapshot_stash_dir=self.stash,
            parents=[snapshots[0]],
            cooperator=self.uncooperator,
        )
        d.addCallback(snapshots.append)

        # serialize and store the snapshot in db.
        # It should rewrite the previously written row.
        self.db.store_local_snapshot(
            snapshots[1],
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )

        # now read back the serialized snapshot from db
        reconstructed_local_snapshot = self.db.get_local_snapshot(filename)

        self.assertThat(
            reconstructed_local_snapshot,
            MatchesStructure(
                relpath=Equals(filename),
                parents_local=HasLength(1)
            )
        )

        # the initial snapshot does not have parent snapshots
        self.assertThat(
            reconstructed_local_snapshot.parents_local[0],
            MatchesStructure(
                parents_local=HasLength(0),
            )
        )

    @given(
        content1=binary(min_size=1),
        content2=binary(min_size=1),
        filename=magic_folder_filenames(),
        stash_subdir=path_segments(),
    )
    def test_store_snapshot_missing_parents(self, content1, content2, filename, stash_subdir):
        """
        Storing a snapshot whose parents are not in the database will raise an
        error.
        """
        data1 = BytesIO(content1)

        snapshots = []

        d = create_snapshot(
            relpath=filename,
            author=self.author,
            data_producer=data1,
            snapshot_stash_dir=self.stash,
            parents=[],
            cooperator=self.uncooperator,
        )
        d.addCallback(snapshots.append)

        # now modify the same file and create a new local snapshot
        data2 = BytesIO(content2)
        d = create_snapshot(
            relpath=filename,
            author=self.author,
            data_producer=data2,
            snapshot_stash_dir=self.stash,
            parents=[snapshots[0]],
            cooperator=self.uncooperator,
        )
        d.addCallback(snapshots.append)

        # serialize and store the snapshot in db.
        # It should rewrite the previously written row.
        with ExpectedException(LocalSnapshotMissingParent):
            self.db.store_local_snapshot(
                snapshots[1],
                PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
            )

    @given(
        local_snapshots(),
    )
    def test_delete_all_local_snapshots_for(self, snapshot):
        """
        After a local snapshot is deleted from the database,
        ``MagicFolderConfig.get_local_snapshot`` raises ``KeyError`` for that
        snapshot's path.
        """
        self.db.store_local_snapshot(
            snapshot,
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )
        self.db.delete_all_local_snapshots_for(snapshot.relpath)
        with ExpectedException(KeyError, escape(repr(snapshot.relpath))):
            self.db.get_local_snapshot(snapshot.relpath)



class DeleteLocalSnapshotTests(SyncTestCase):
    """
    Test the 'delete single snapshot' codepaths in MagicFolderConfig

    non-Hypothesis-using tests
    """
    def setUp(self):
        super(DeleteLocalSnapshotTests, self).setUp()
        self.author = create_local_author(u"alice")

        self.temp = FilePath(self.mktemp())
        self.stash = self.temp.child("stash")
        self.stash.makedirs()
        self.magic = self.temp.child("magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            # collective dircap
            random_dircap(readonly=True),
            # upload dircap
            random_dircap(),
            self.magic,
            60,
            60,
        )

        self.snap0 = LocalSnapshot(
            relpath="foo",
            author=self.author,
            metadata=dict(),
            content_path=FilePath("snap0 content"),
            parents_local=[],
            parents_remote=[],
        )
        self.db.store_local_snapshot(
            self.snap0,
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )

        self.snap1 = LocalSnapshot(
            relpath="foo",
            author=self.author,
            metadata=dict(),
            content_path=FilePath("snap1 content"),
            parents_local=[self.snap0],
            parents_remote=[],
        )
        self.db.store_local_snapshot(
            self.snap1,
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )

        self.snap2 = LocalSnapshot(
            relpath="foo",
            author=self.author,
            metadata=dict(),
            content_path=FilePath("snap2 content"),
            parents_local=[self.snap1],
            parents_remote=[],
        )
        self.db.store_local_snapshot(
            self.snap2,
            PathState(42, seconds_to_ns(42), seconds_to_ns(42)),
        )

    def test_delete_one_local_snapshot(self):
        """
        Given a chain of three snapshots deleting the oldest one results
        in a proper chain of two snapshots.
        """
        # we have a "leaf" snapshot "snap2" with parent "snap1" and
        # grandparent "snap0" snap2->snap1->snap0

        # pretend we uploaded the oldest ancestor, the only one we
        # _can_ upload (semantically)
        remote0 = RemoteSnapshot(
            self.snap0.relpath,
            self.snap0.author,
            {
                "relpath": self.snap0.relpath,
                "modification_time": 1234,
            },
            capability=random_immutable(directory=True),
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )

        self.db.delete_local_snapshot(self.snap0, remote0)

        # we should still have a 3-snapshot chain, but there should be
        # only 2 local snapshots and one remote

        # start with the "leaf", the most-local snapshot
        dbsnap2 = self.db.get_local_snapshot(self.snap0.relpath)
        self.assertThat(
            dbsnap2.content_path,
            Equals(FilePath("snap2 content"))
        )
        self.assertThat(
            dbsnap2.parents_local,
            AfterPreprocessing(len, Equals(1)),
        )
        self.assertThat(
            dbsnap2.parents_remote,
            AfterPreprocessing(len, Equals(0)),
        )

        # the leaf had just one parent, which is local -- examine it
        dbsnap1 = dbsnap2.parents_local[0]
        self.assertThat(
            dbsnap1.content_path,
            Equals(FilePath("snap1 content"))
        )
        self.assertThat(
            dbsnap1.parents_local,
            AfterPreprocessing(len, Equals(0)),
        )
        self.assertThat(
            dbsnap1.parents_remote,
            AfterPreprocessing(len, Equals(1)),
        )

        # the "middle" parent (above) has no local parents and one
        # remote, which is correct .. the final parent should be the
        # one we replaced the local with.
        self.assertThat(
            dbsnap1.parents_remote[0],
            Equals(remote0.capability),
        )

    def test_delete_several_local_snapshots(self):
        """
        Given a chain of three snapshots deleting them all results in now
        snapshots.
        """
        # we have a "leaf" snapshot "snap2" with parent "snap1" and
        # grandparent "snap0" snap2->snap1->snap0

        # pretend we uploaded the oldest ancestor, the only one we
        # _can_ upload (semantically)
        remote0 = RemoteSnapshot(
            self.snap0.relpath,
            self.snap0.author,
            {
                "relpath": self.snap0.relpath,
                "modification_time": 1234,
            },
            capability=random_immutable(directory=True),
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )

        self.db.delete_local_snapshot(self.snap0, remote0)
        self.db.delete_local_snapshot(self.snap1, remote0)
        self.db.delete_local_snapshot(self.snap2, remote0)

        try:
            self.db.get_local_snapshot(self.snap0.relpath)
            assert False, "expected no local snapshots"
        except KeyError:
            pass

    def test_delete_snapshot_twice(self):
        """
        Attempting to delete a snapshot that doesn't exist is an error.
        """
        remote0 = RemoteSnapshot(
            self.snap0.relpath,
            self.snap0.author,
            {
                "relpath": self.snap0.relpath,
                "modification_time": 1234,
            },
            capability=random_immutable(directory=True),
            parents_raw=[],
            content_cap=random_immutable(),
            metadata_cap=random_immutable(),
        )

        self.db.delete_local_snapshot(self.snap0, remote0)

        try:
            self.db.delete_local_snapshot(self.snap0, remote0)
            assert False, "Shouldn't be able to delete unfound snapshot"
        except ValueError:
            pass

    def test_delete_no_snapshots_for_relpath(self):
        """
        Attempting to delete an unknown relpath is an error.
        """
        snap = LocalSnapshot(
            relpath="non-existent",
            author=self.author,
            metadata=dict(),
            content_path=FilePath("snap0 content"),
            parents_local=[],
            parents_remote=[],
        )
        remote = RemoteSnapshot(
            snap.relpath,
            snap.author,
            {
                "relpath": snap.relpath,
                "modification_time": 1234,
            },
            capability="URI:DIR2-CHK:aaaa:aaaa",
            parents_raw=[],
            content_cap="URI:CHK:bbbb:bbbb",
            metadata_cap="URI:CHK:cccc:cccc",
        )

        try:
            self.db.delete_local_snapshot(snap, remote)
            assert False, "Shouldn't be able to delete unfound snapshot"
        except KeyError:
            pass


class MagicFolderConfigCurrentSnapshotTests(SyncTestCase):
    """
    Tests for the ``MagicFolderConfig`` APIs that deal with current snapshots.
    """
    def setUp(self):
        super(MagicFolderConfigCurrentSnapshotTests, self).setUp()
        self.author = create_local_author(u"alice")

    def setup_example(self):
        self.temp = FilePath(self.mktemp())
        self.stash = self.temp.child("stash")
        self.stash.makedirs()
        self.magic = self.temp.child("magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            random_dircap(readonly=True),
            random_dircap(),
            self.magic,
            60,
            60,
        )

    @given(
        remote_snapshots(),
        path_states(),
    )
    def test_remotesnapshot_roundtrips(self, snapshot, path_state):
        """
        The capability for a ``RemoteSnapshot`` added with
        ``MagicFolderConfig.store_downloaded_snapshot`` can be read back with
        ``MagicFolderConfig.get_remotesnapshot``.
        """
        self.db.store_downloaded_snapshot(snapshot.relpath, snapshot, path_state)
        capability = self.db.get_remotesnapshot(snapshot.relpath)
        db_path_state = self.db.get_currentsnapshot_pathstate(snapshot.relpath)
        self.assertThat(
            (capability, db_path_state),
            Equals((snapshot.capability, path_state))
        )

    @given(
        relative_paths(),
        remote_snapshots(),
        path_states(),
    )
    def test_remotesnapshot_with_existing_state(self, relpath, snapshot, path_state):
        """
        A ``RemoveSnapshot`` can be added without path state, if existing path
        state is in the database.
        """
        self.db.store_currentsnapshot_state(relpath, path_state)
        self.db.store_downloaded_snapshot(relpath, snapshot, path_state)
        capability = self.db.get_remotesnapshot(relpath)
        db_path_state = self.db.get_currentsnapshot_pathstate(relpath)
        self.assertThat(
            (capability, db_path_state),
            Equals((snapshot.capability, path_state))
        )

    @given(
        relative_paths(),
        remote_snapshots(),
    )
    def test_store_remote_without_state(self, relpath, snapshot):
        """
        Calling :py:`MagicFolderConfig.store_uploaded_snapshot` without a path
        state, when there isn't already corresponding path state fails.
        """
        with ExpectedException(RemoteSnapshotWithoutPathState):
            self.db.store_uploaded_snapshot(relpath, snapshot, 42)

    @given(
        path_segments(),
    )
    def test_remotesnapshot_not_found(self, path):
        """
        ``MagicFolderConfig.get_remotesnapshot`` raises ``KeyError`` if there is
        no known remote snapshot for the given path.
        """
        with ExpectedException(KeyError, escape(repr(path))):
            self.db.get_remotesnapshot(path)
        with ExpectedException(KeyError, escape(repr(path))):
            self.db.get_currentsnapshot_pathstate(path)

    @given(
        path_segments(),
        path_states(),
    )
    def test_remotesnapshot_not_found_with_state(self, path, path_state):
        """
        ``MagicFolderConfig.get_remotesnapshot`` raises ``KeyError`` if there
        is no known remote snapshot for the given path, but there is path state
        for it.
        """
        self.db.store_currentsnapshot_state(path, path_state)
        with ExpectedException(KeyError, escape(repr(path))):
            self.db.get_remotesnapshot(path)

    @given(
        # Get two RemoteSnapshots with the same path.
        path_segments().flatmap(
            lambda path: tuples(
                just(path),
                lists(
                    remote_snapshots(relpaths=just(path)),
                    min_size=2,
                    max_size=2,
                ),
            ),
        ),
        path_states(),
    )
    def test_replace_remotesnapshot(self, snapshots, path_state):
        """
        A ``RemoteSnapshot`` for a given path can be replaced by a new
        ``RemoteSnapshot`` for the same path, without providing path state.
        """
        path, snapshots = snapshots
        self.db.store_downloaded_snapshot(path, snapshots[0], path_state)
        self.db.store_uploaded_snapshot(path, snapshots[1], 42)
        capability = self.db.get_remotesnapshot(path)
        db_path_state = self.db.get_currentsnapshot_pathstate(path)
        self.assertThat(
            (capability, db_path_state),
            Equals((snapshots[1].capability, path_state))
        )

    @given(
        # Get two RemoteSnapshots with the same path.
        path_segments().flatmap(
            lambda path: tuples(
                just(path),
                lists(
                    remote_snapshots(relpaths=just(path)),
                    min_size=2,
                    max_size=2,
                ),
            ),
        ),
        lists(path_states(), min_size=2, max_size=2)
    )
    def test_replace_remotesnapshot_with_state(self, snapshots, path_states):
        """
        A ``RemoteSnapshot`` for a given path can be replaced by a new
        ``RemoteSnapshot`` for the same path, when providing path state.
        """
        path, snapshots = snapshots
        self.db.store_downloaded_snapshot(path, snapshots[0], path_states[0])
        self.db.store_downloaded_snapshot(path, snapshots[1], path_states[1])
        capability = self.db.get_remotesnapshot(path)
        db_path_state = self.db.get_currentsnapshot_pathstate(path)
        self.assertThat(
            (capability, db_path_state),
            Equals((snapshots[1].capability, path_states[1]))
        )

    @given(
        # Get two RemoteSnapshots with the same path.
        path_segments(),
        lists(path_states(), min_size=2, max_size=2)
    )
    def test_replace_path_state(self, path, path_states):
        """
        A ``RemoteSnapshot`` for a given path can be replaced by a new
        ``RemoteSnapshot`` for the same path, when providing path state.
        """
        self.db.store_currentsnapshot_state(path, path_states[0])
        self.db.store_currentsnapshot_state(path, path_states[1])
        db_path_state = self.db.get_currentsnapshot_pathstate(path)
        self.assertThat(
            db_path_state,
            Equals(path_states[1])
        )

    @given(
        lists(path_segments(), min_size=1, unique=True),
        lists(path_states(), min_size=1),
    )
    def test_all_path_status(self, paths, path_states):
        """
        We can recover all path-statuses
        """
        # maybe there's a way to make hypothesis make same-sized lists?
        size = min(len(paths), len(path_states))
        paths = paths[:size]
        path_states = path_states[:size]

        self.db._get_current_timestamp = lambda: 1234
        for p, ps in zip(paths, path_states):
            self.db.store_currentsnapshot_state(p, ps)

        self.assertThat(
            self.db.get_all_current_snapshot_pathstates(),
            Equals([
                (p, ps, seconds_to_ns(1234), None)
                for p, ps in zip(paths, path_states)
            ]),
        )

    def test_remotesnapshot_caps_missing(self):
        """
        A KeyError is thrown accessing missing remotesnapshot_caps
        """
        self.setup_example()
        with self.assertRaises(KeyError):
            self.db.get_remotesnapshot_caps("a-missing-snapshot-name")

    def test_tahoe_object_sizes(self):
        """
        Correct capability sizes are returned
        """
        self.setup_example()
        self.assertThat(
            self.db.get_tahoe_object_sizes(),
            Equals([])
        )

    @given(
        relative_paths(),
        path_states(),
    )
    def test_tahoe_object_sizes_local(self, relpath, state):
        """
        Local-only snapshots get no size returned
        """
        self.db.store_currentsnapshot_state(relpath, state)
        self.assertThat(
            self.db.get_tahoe_object_sizes(),
            Equals([])
        )

    @given(
        relative_paths(),
        remote_snapshots(),
        path_states(),
    )
    def test_tahoe_object_sizes_remote(self, relpath, remote_snap, state):
        """
        Correct capability sizes are returned
        """
        self.db.store_downloaded_snapshot(relpath, remote_snap, state)

        s = remote_snap.capability.size
        c = remote_snap.content_cap.size
        m = remote_snap.metadata_cap.size
        self.assertThat(
            self.db.get_tahoe_object_sizes(),
            Equals([s, c, m])
        )


class RemoteSnapshotTimeTests(SyncTestCase):
    """
    Test RemoteSnapshot timestamps
    """
    def setUp(self):
        super(RemoteSnapshotTimeTests, self).setUp()
        self.author = create_local_author(u"alice")
        self.temp = FilePath(self.mktemp())
        self.stash = self.temp.child("stash")
        self.stash.makedirs()
        self.magic = self.temp.child("magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            random_dircap(readonly=True),
            random_dircap(),
            self.magic,
            60,
            60,
        )

    def test_limit(self):
        """
        Add 35 RemoteSnapshots and ensure we only get 30 back from
        'recent' list.
        """
        self.patch(self.db, '_get_current_timestamp', partial(next, itertools.count()))
        for x in range(35):
            relpath = "foo_{}".format(x)
            remote = RemoteSnapshot(
                relpath,
                self.author,
                {"relpath": relpath, "modification_time": x},
                random_immutable(directory=True),
                [],
                random_immutable(),
                random_immutable(),
            )
            # XXX this seems fraught; have to remember to call two
            # APIs or we get exceptions / inconsistent state...
            self.db.store_currentsnapshot_state(relpath, PathState(0, seconds_to_ns(x), seconds_to_ns(x)))
            self.db.store_uploaded_snapshot(relpath, remote, 0)

        # on windows the timestamps end up being long() instead of
        # int() is why the pre-processing includes an int() on the
        # second tuple element
        self.assertThat(
            self.db.get_recent_remotesnapshot_paths(20),
            AfterPreprocessing(
                lambda data: [
                    (t[0], int(t[1])) for t in data
                ],
                Equals([
                    ("foo_{}".format(x), x)
                    for x in range(34, 4, -1)  # newest to oldest
                ])
            )
        )


class ConflictTests(SyncTestCase):
    """
    Test conflicts
    """
    def setUp(self):
        super(ConflictTests, self).setUp()
        self.author = create_local_author(u"desktop")
        self.temp = FilePath(self.mktemp())
        self.stash = self.temp.child("stash")
        self.stash.makedirs()
        self.magic = self.temp.child("magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            random_dircap(readonly=True),
            random_dircap(),
            self.magic,
            60,
            60,
        )

    @given(
        tahoe_lafs_immutable_dir_capabilities(),
        tahoe_lafs_chk_capabilities(),
        tahoe_lafs_chk_capabilities(),
    )
    def test_add_list_conflict(self, remote_cap, meta_cap, content_cap):
        """
        Adding a conflict allows us to list it
        """
        snap = RemoteSnapshot(
            "foo",
            self.author,
            {"relpath": "foo", "modification_time": 1234},
            remote_cap,
            [],
            content_cap,
            meta_cap,
        )

        self.db.add_conflict(snap)
        self.assertThat(
            self.db.list_conflicts(),
            Equals({
                "foo": [Conflict(remote_cap, self.author.name)],
            }),
        )
        self.db.resolve_conflict("foo")

    @given(
        tahoe_lafs_immutable_dir_capabilities(),
        tahoe_lafs_chk_capabilities(),
        tahoe_lafs_chk_capabilities(),
    )
    def test_add_conflict_twice(self, remote_cap, meta_cap, content_cap):
        """
        It's an error to add the same conflict twice
        """
        snap = RemoteSnapshot(
            "foo",
            self.author,
            {"relpath": "foo", "modification_time": 1234},
            remote_cap,
            [],
            content_cap,
            meta_cap,
        )

        self.db.add_conflict(snap)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_conflict(snap)
        self.db.resolve_conflict("foo")

    @given(
        tahoe_lafs_immutable_dir_capabilities(),
        tahoe_lafs_immutable_dir_capabilities(),
        tahoe_lafs_chk_capabilities(),
        tahoe_lafs_chk_capabilities(),
    )
    def test_add_list_multi_conflict(self, remote0_cap, remote1_cap, meta_cap, content_cap):
        """
        A multiple-conflict is reflected in the list
        """
        assume(remote0_cap != remote1_cap)

        snap0 = RemoteSnapshot(
            "foo",
            create_local_author(u"desktop"),
            {"relpath": "foo", "modification_time": 1234},
            remote0_cap,
            [],
            content_cap,
            meta_cap,
        )
        snap1 = RemoteSnapshot(
            "foo",
            create_local_author(u"laptop"),
            {"relpath": "foo", "modification_time": 1234},
            remote1_cap,
            [],
            content_cap,
            meta_cap,
        )

        self.db.add_conflict(snap0)
        self.db.add_conflict(snap1)
        self.assertThat(
            self.db.list_conflicts(),
            Equals({
                "foo": [
                    Conflict(remote0_cap, "desktop"),
                    Conflict(remote1_cap, "laptop"),
                ],
            })
        )
        self.db.resolve_conflict("foo")

    @given(
        tahoe_lafs_immutable_dir_capabilities(),
        tahoe_lafs_immutable_dir_capabilities(),
        tahoe_lafs_chk_capabilities(),
    )
    def test_delete_multi_conflict(self, remote0_cap, remote1_cap, immutable_cap):
        """
        A multiple-conflict is successfully deleted
        """

        snap0 = RemoteSnapshot(
            "foo",
            create_local_author(u"laptop"),
            {"relpath": "foo", "modification_time": 1234},
            remote0_cap,
            [],
            immutable_cap,
            immutable_cap,
        )
        snap1 = RemoteSnapshot(
            "foo",
            create_local_author(u"phone"),
            {"relpath": "foo", "modification_time": 1234},
            remote1_cap,
            [],
            immutable_cap,
            immutable_cap,
        )

        self.db.add_conflict(snap0)
        self.db.add_conflict(snap1)
        self.assertThat(
            self.db.list_conflicts(),
            Equals({
                "foo": [
                    Conflict(remote0_cap, "laptop"),
                    Conflict(remote1_cap, "phone"),
                ]
            }),
        )

        self.db.resolve_conflict("foo")
        self.assertThat(
            self.db.list_conflicts(),
            Equals(dict()),
        )

    @given(
        relative_paths(),
        author_names(),
    )
    def test_conflict_file(self, relpath, author):
        """
        A conflict-file is detected as one.
        """
        conflict_path = self.db.magic_path.preauthChild(
            "{}.conflict-{}".format(relpath, author)
        )

        self.assertThat(
            self.db.is_conflict_marker(conflict_path),
            Equals(True)
        )
