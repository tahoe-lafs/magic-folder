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

from .common import (
    AsyncTestCase,
    SyncTestCase,
)
from magic_folder.util.observer import (
    ListenObserver,
)
from magic_folder.initialize import (
    magic_folder_initialize,
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
