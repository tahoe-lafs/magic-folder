from shutil import (
    rmtree,
)
from os import (
    mkdir,
)

from twisted.python.filepath import (
    FilePath,
)

from .common import (
    SyncTestCase,
)
from ..config import (
    create_global_configuration,
)

class TestGlobalConfig(SyncTestCase):

    def setUp(self):
        super(TestGlobalConfig, self).setUp()
        self.temp = FilePath(self.mktemp())

    def tearDown(self):
        super(TestGlobalConfig, self).tearDown()
        rmtree(self.temp.path)

    def test_create(self):
        create_global_configuration(self.temp, "tcp:1234")
