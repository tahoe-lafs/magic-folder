
from twisted.python.filepath import (
    FilePath,
)

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    one_of,
    just,
)

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    Equals,
    NotEquals,
    Contains,
    MatchesStructure,
)

from hyperlink import (
    DecodedURL,
    URL,
)

import sqlite3

from .common import (
    SyncTestCase,
)
from .fixtures import (
    NodeDirectory,
)
from .strategies import (
    path_segments_without_dotfiles,
    port_numbers,
    interfaces,
)
from ..config import (
    endpoint_description_to_http_api_root,
    create_global_configuration,
    load_global_configuration,
    ConfigurationError,
)
from ..snapshot import (
    create_local_author,
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
        config = create_global_configuration(confdir, u"tcp:1234", self.node_dir)
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
        with ExpectedException(ValueError, ".*{}.*".format(self.temp.path)):
            create_global_configuration(self.temp, u"tcp:1234", self.node_dir)

    def test_load_db(self):
        """
        ``load_global_configuration`` can read the global configuration written by
        ``create_global_configuration``.
        """
        create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
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
        with ExpectedException(ValueError, ".*{}.*".format(non_dir.path)):
            load_global_configuration(non_dir)

    def test_rotate_api_key(self):
        """
        ``GlobalConfigDatabase.rotate_api_token`` replaces the current API token
        with a new one.
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
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
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
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

    def test_database_wrong_version(self):
        """
        ``load_global_configuration`` raises ``ConfigurationError`` if asked to
        load a database that has a version other than ``1``.
        """
        create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
        # make the version "0", which will never happen for real
        # because we'll keep incrementing the version from 1
        db_fname = self.temp.child("global.sqlite")
        with sqlite3.connect(db_fname.path) as connection:
            cursor = connection.cursor()
            cursor.execute("UPDATE version SET version=?", (0, ))

        with ExpectedException(ConfigurationError):
            load_global_configuration(self.temp)


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


class TestMagicFolderConfig(SyncTestCase):

    def setUp(self):
        super(TestMagicFolderConfig, self).setUp()
        self.temp = FilePath(self.mktemp())
        self.node_dir = FilePath(self.mktemp())
        self.tahoe_dir = self.useFixture(NodeDirectory(self.node_dir))

    def test_create_folder(self):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        magic_folder = config.create_magic_folder(
            u"foo",
            magic,
            self.temp.child("state"),
            alice,
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
        )
        self.assertThat(
            magic_folder.author,
            Equals(alice),
        )

    def test_create_folder_duplicate(self):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        magic.makedirs()
        config.create_magic_folder(
            u"foo",
            magic,
            self.temp.child("state"),
            alice,
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
        )
        with ExpectedException(ValueError, "Already have a magic-folder named 'foo'"):
            config.create_magic_folder(
                u"foo",
                magic,
                self.temp.child("state2"),
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
            )

    def test_folder_nonexistant_magic_path(self):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        with ExpectedException(ValueError, ".*{}.*".format(magic.path)):
            config.create_magic_folder(
                u"foo",
                magic,
                self.temp.child("state"),
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
            )

    def test_folder_state_already_exists(self):
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        state = self.temp.child("state")
        magic.makedirs()
        state.makedirs()  # shouldn't pre-exist, though
        with ExpectedException(ValueError, ".*{}.*".format(state.path)):
            config.create_magic_folder(
                u"foo",
                magic,
                state,
                alice,
                u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
                u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
                60,
            )

    def test_folder_get_path(self):
        """
        we can retrieve the stash-path from a magic-folder-confgi
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        state = self.temp.child("state")
        magic.makedirs()
        config.create_magic_folder(
            u"foo",
            magic,
            state,
            alice,
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            60,
        )
        self.assertThat(config.list_magic_folders(), Contains(u"foo"))
        mf_config = config.get_magic_folder(u"foo")
        self.assertThat(
            mf_config.stash_path,
            Equals(state.child("stash"))
        )

    def test_get_folder_nonexistent(self):
        """
        an error to retrieve a non-existent folder
        """
        config = create_global_configuration(self.temp, u"tcp:1234", self.node_dir)
        with ExpectedException(ValueError):
            config.get_magic_folder(u"non-existent")
