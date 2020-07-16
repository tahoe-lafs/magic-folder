from shutil import (
    rmtree,
)
from os import (
    mkdir,
)

from twisted.python.filepath import (
    FilePath,
)

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    Equals,
    NotEquals,
    Contains,
)

from .common import (
    SyncTestCase,
)
from ..config import (
    create_global_configuration,
    load_global_configuration,
)
from ..snapshot import (
    create_local_author,
)


class TestGlobalConfig(SyncTestCase):

    def setUp(self):
        super(TestGlobalConfig, self).setUp()
        self.temp = FilePath(self.mktemp())

    def test_create(self):
        create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")

    def test_create_existing_dir(self):
        mkdir(self.temp.path)
        with ExpectedException(ValueError, ".*{}.*".format(self.temp.path)):
            create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")

    def test_load_db(self):
        create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
        config = load_global_configuration(self.temp)
        self.assertThat(
            config.api_endpoint,
            Equals("tcp:1234")
        )

    def test_load_db_no_such_directory(self):
        non_dir = self.temp.child("non-existent")
        with ExpectedException(ValueError, ".*{}.*".format(non_dir.path)):
            load_global_configuration(non_dir)

    def test_rotate_api_key(self):
        config = create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
        pre = config.api_token
        config.rotate_api_token()
        self.assertThat(
            config.api_token,
            NotEquals(pre)
        )

    def test_change_api_endpoint(self):
        config = create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
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


class TestMagicFolderConfig(SyncTestCase):

    def setUp(self):
        super(TestMagicFolderConfig, self).setUp()
        self.temp = FilePath(self.mktemp())

    def test_create_folder(self):
        config = create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        mkdir(magic.path)
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
        config = create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        mkdir(magic.path)
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
        config = create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
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
        config = create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        state = self.temp.child("state")
        mkdir(magic.path)
        mkdir(state.path)  # shouldn't pre-exist
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
        config = create_global_configuration(self.temp, "tcp:1234", "http://localhost:3456")
        alice = create_local_author("alice")
        magic = self.temp.child("magic")
        state = self.temp.child("state")
        mkdir(magic.path)
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
