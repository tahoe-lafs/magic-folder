from shutil import (
    rmtree,
)

from twisted.internet.interfaces import (
    IStreamServerEndpoint,
)
from twisted.internet.defer import (
    succeed,
    failure,
)
from twisted.python.filepath import (
    FilePath,
)
from zope.interface import (
    implementer,
)

import attr

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    Equals,
)
from .common import (
    AsyncTestCase,
    SyncTestCase,
)
from .fixtures import (
    NodeDirectory,
)
from ..config import (
    load_global_configuration,
)
from magic_folder.util.observer import (
    ListenObserver,
)
from magic_folder.initialize import (
    magic_folder_initialize,
)
from magic_folder.migrate import (
    magic_folder_migrate,
)


@attr.s
@implementer(IStreamServerEndpoint)
class EndpointForTesting(object):
    _responses = attr.ib(default=attr.Factory(list))

    def listen(self, factory):
        if not self._responses:
            return failure(Exception("no more responses"))
        r = self._responses[0]
        self._responses = self._responses[1:]
        return succeed(r)


class TestListenObserver(AsyncTestCase):
    """
    Confirm operation of magic_folder.util.observer.ListenObserver
    """

    def test_good(self):
        ep = EndpointForTesting(["we listened"])
        obs = ListenObserver(endpoint=ep)
        d0 = obs.observe()
        d1 = obs.observe()

        self.assertFalse(d0.called)
        self.assertFalse(d1.called)


        result = obs.listen("not actually a factory")
        self.assertTrue(result.called)
        self.assertEqual(result.result, "we listened")

        d2 = obs.observe()

        for d in [d0, d1, d2]:
            self.assertTrue(d.called)
            self.assertEqual(d.result, "we listened")


class TestInitialize(SyncTestCase):
    """
    Confirm operation of 'magic-folder initialize' command
    """

    def setUp(self):
        super(TestInitialize, self).setUp()
        self.temp = FilePath(self.mktemp())

    def tearDown(self):
        super(TestInitialize, self).tearDown()
        rmtree(self.temp.path, ignore_errors=True)

    def test_good(self):
        magic_folder_initialize(self.temp.path, u"tcp:1234", u"http://example.com")


class TestMigrate(SyncTestCase):
    """
    Confirm operation of 'magic-folder migrate' command
    """

    def setUp(self):
        super(TestMigrate, self).setUp()
        self.temp = FilePath(self.mktemp())
        from twisted.internet import reactor
        self.magic_path = self.temp.child("magic")
        self.magic_path.makedirs()
        self.node_dir = self.useFixture(NodeDirectory(self.temp.child("node")))
        self.node_dir.create_magic_folder(
            u"test-folder",
            u"URI:DIR2-RO:ou5wvazwlyzmqw7yof5ifmgmau:xqzt6uoulu4f3m627jtadpofnizjt3yoewzeitx47vw6memofeiq",
            u"URI:DIR2:bgksdpr3lr2gvlvhydxjo2izea:dfdkjc44gg23n3fxcxd6ywsqvuuqzo4nrtqncrjzqmh4pamag2ia",
            self.magic_path,
            60,
        )

    def test_good(self):
        magic_folder_migrate(
            self.temp.child("new_magic"),
            u"tcp:1234",
            self.node_dir.path,
            u"alice",
        )
        config = load_global_configuration(self.temp.child("new_magic"))
        self.assertThat(
            list(config.list_magic_folders()),
            Equals([u"test-folder"]),
        )
