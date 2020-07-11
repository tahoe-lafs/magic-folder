from shutil import (
    rmtree,
)
from os import (
    mkdir,
)

from twisted.python.filepath import (
    FilePath,
)

from testtools.matchers import (
    Equals,
)

from .common import (
    SyncTestCase,
)
from ..config import (
    create_global_configuration,
    load_global_configuration,
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

    def test_load_db(self):
        create_global_configuration(self.temp, "tcp:1234")
        config = load_global_configuration(self.temp)
        self.assertThat(
            config.api_endpoint,
            Equals("tcp:1234")
        )
