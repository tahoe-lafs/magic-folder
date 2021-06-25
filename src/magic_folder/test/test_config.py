from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

from io import (
    BytesIO,
)
from re import (
    escape,
)

from twisted.python.filepath import (
    FilePath,
)

from hypothesis import (
    given,
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
    path_segments,
    path_segments_without_dotfiles,
    port_numbers,
    interfaces,
    magic_folder_filenames,
    remote_snapshots,
    local_snapshots,
    folder_names
)
from ..common import InvalidMagicFolderName, APIError
from ..config import (
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
        confdir = self.temp.child(b"base")
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
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
            0,
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
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
            0,
        )
        with ExpectedException(APIError, "Already have a magic-folder named 'foo'"):
            config.create_magic_folder(
                u"foo",
                magic,
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
                0,
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
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
            0,
        )
        config.create_magic_folder(
            u"foo.",
            magic,
            alice,
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
            0,
        )
        config.create_magic_folder(
            u"foo ",
            magic,
            alice,
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
            0,
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
                0,
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
                0,
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
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
            0,
        )
        self.assertThat(config.list_magic_folders(), Contains(u"foo"))
        mf_config = config.get_magic_folder(u"foo")
        self.assertThat(
            mf_config.stash_path,
            Equals(config._get_state_path(name).child(u"stash")),
        )

    def test_get_folder_nonexistent(self):
        """
        an error to retrieve a non-existent folder
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir, u"tcp:localhost:1234")
        with ExpectedException(ValueError):
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
                0,
            )


class StoreLocalSnapshotTests(SyncTestCase):
    """
    Tests for the ``MagicFolderConfig`` APIs which store and load
    ``LocalSnapshot`` objects.
    """
    def setUp(self):
        super(StoreLocalSnapshotTests, self).setUp()
        self.author = create_local_author(u"alice")

    def setup_example(self):
        self.temp = FilePath(self.mktemp())
        self.stash = self.temp.child("stash")
        self.stash.makedirs()
        self.magic = self.temp.child(b"magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            self.magic,
            60,
            0,
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
            name=filename,
            author=self.author,
            data_producer=data1,
            snapshot_stash_dir=self.stash,
            parents=[],
        )
        d.addCallback(snapshots.append)

        self.assertThat(
            d,
            succeeded(Always()),
        )

        self.db.store_local_snapshot(snapshots[0])

        # now modify the same file and create a new local snapshot
        data2 = BytesIO(content2)
        d = create_snapshot(
            name=filename,
            author=self.author,
            data_producer=data2,
            snapshot_stash_dir=self.stash,
            parents=[snapshots[0]],
        )
        d.addCallback(snapshots.append)

        # serialize and store the snapshot in db.
        # It should rewrite the previously written row.
        self.db.store_local_snapshot(snapshots[1])

        # now read back the serialized snapshot from db
        reconstructed_local_snapshot = self.db.get_local_snapshot(filename)

        self.assertThat(
            reconstructed_local_snapshot,
            MatchesStructure(
                name=Equals(filename),
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
        local_snapshots(),
    )
    def test_delete_localsnapshot(self, snapshot):
        """
        After a local snapshot is deleted from the database,
        ``MagicFolderConfig.get_local_snapshot`` raises ``KeyError`` for that
        snapshot's path.
        """
        self.db.store_local_snapshot(snapshot)
        self.db.delete_localsnapshot(snapshot.name)
        with ExpectedException(KeyError, escape(repr(snapshot.name))):
            self.db.get_local_snapshot(snapshot.name)


class MagicFolderConfigRemoteSnapshotTests(SyncTestCase):
    """
    Tests for the ``MagicFolderConfig`` APIs that deal with remote snapshots.
    """
    def setUp(self):
        super(MagicFolderConfigRemoteSnapshotTests, self).setUp()
        self.author = create_local_author(u"alice")

    def setup_example(self):
        self.temp = FilePath(self.mktemp())
        self.stash = self.temp.child("stash")
        self.stash.makedirs()
        self.magic = self.temp.child(b"magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            self.magic,
            60,
            0,
        )

    @given(
        remote_snapshots(),
    )
    def test_remotesnapshot_roundtrips(self, snapshot):
        """
        The capability for a ``RemoteSnapshot`` added with
        ``MagicFolderConfig.store_remotesnapshot`` can be read back with
        ``MagicFolderConfig.get_remotesnapshot``.
        """
        self.db.store_remotesnapshot(snapshot.name, snapshot)
        loaded = self.db.get_remotesnapshot(snapshot.name)
        self.assertThat(
            snapshot.capability,
            Equals(loaded),
        )

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

    @given(
        # Get two RemoteSnapshots with the same path.
        path_segments().flatmap(
            lambda path: lists(
                remote_snapshots(names=just(path)),
                min_size=2,
                max_size=2,
            ),
        ),
    )
    def test_replace_remotesnapshot(self, snapshots):
        """
        A ``RemoteSnapshot`` for a given path can be replaced by a new
        ``RemoteSnapshot`` for the same path.
        """
        path = snapshots[0].name
        self.db.store_remotesnapshot(path, snapshots[0])
        self.db.store_remotesnapshot(path, snapshots[1])
        loaded = self.db.get_remotesnapshot(path)
        self.assertThat(
            snapshots[1].capability,
            Equals(loaded),
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
        self.magic = self.temp.child(b"magic")
        self.magic.makedirs()

        self.db = MagicFolderConfig.initialize(
            u"some-folder",
            SQLite3DatabaseLocation.memory(),
            self.author,
            self.stash,
            u"URI:DIR2-RO:aaa:bbb",
            u"URI:DIR2:ccc:ddd",
            self.magic,
            60,
            0,
        )

    def test_limit(self):
        """
        Add 35 RemoteSnapshots and ensure we only get 30 back from
        'recent' list.
        """
        for x in range(35):
            name = "foo_{}".format(x)
            remote = RemoteSnapshot(
                name,
                self.author,
                {"modification_time": x},
                "URI:DIR2-CHK:",
                [],
                "URI:DIR2-CHK:",
            )
            self.db.store_remotesnapshot(name, remote)

        self.assertThat(
            self.db.get_recent_remotesnapshot_paths(20),
            Equals([
                ("foo_{}".format(x), x)
                for x in range(34, 4, -1)  # newest to oldest
            ])
        )
