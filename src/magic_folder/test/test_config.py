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

    def tearDown(self):
        super(TestGlobalConfig, self).tearDown()
        rmtree(self.temp.path)

    def test_create(self):
        create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")

    def test_load_db(self):
        create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")
        config = load_global_configuration(self.temp)
        self.assertThat(
            config.api_endpoint,
            Equals("tcp:1234")
        )

    def test_rotate_api_key(self):
        config = create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")
        pre = config.api_token
        config.rotate_api_token()
        self.assertThat(
            config.api_token,
            NotEquals(pre)
        )

    def test_change_api_endpoint(self):
        config = create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")
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

    def test_create_folder(self):
        config = create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")
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
        config = create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")
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
        config = create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")
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
        config = create_global_configuration(self.temp, "tcp:1234", "tcp:localhost:5678")
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
