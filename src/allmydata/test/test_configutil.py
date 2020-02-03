import os.path

from twisted.trial import unittest

from allmydata.util import configutil
from allmydata.test.no_network import GridTestMixin
from ..scripts import create_node
from .. import client


class ConfigUtilTests(GridTestMixin, unittest.TestCase):
    def setUp(self):
        super(ConfigUtilTests, self).setUp()
        self.static_valid_config = configutil.ValidConfiguration(
            dict(node=['valid']),
        )
        self.dynamic_valid_config = configutil.ValidConfiguration(
            dict(),
            lambda section_name: section_name == "node",
            lambda section_name, item_name: (section_name, item_name) == ("node", "valid"),
        )

    def test_config_utils(self):
        self.basedir = "cli/ConfigUtilTests/test-config-utils"
        self.set_up_grid(oneshare=True)
        tahoe_cfg = os.path.join(self.get_clientdir(i=0), "tahoe.cfg")

        # test that at least one option was read correctly
        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "nickname"), "client-0")

        # test that set_config can mutate an existing option
        configutil.set_config(config, "node", "nickname", "Alice!")
        configutil.write_config(tahoe_cfg, config)

        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "nickname"), "Alice!")

        # test that set_config can set a new option
        descriptor = "Twas brillig, and the slithy toves Did gyre and gimble in the wabe"
        configutil.set_config(config, "node", "descriptor", descriptor)
        configutil.write_config(tahoe_cfg, config)

        config = configutil.get_config(tahoe_cfg)
        self.failUnlessEqual(config.get("node", "descriptor"), descriptor)

    def test_config_validation_success(self):
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\n')

        config = configutil.get_config(fname)
        # should succeed, no exceptions
        configutil.validate_config(
            fname,
            config,
            self.static_valid_config,
        )

    def test_config_dynamic_validation_success(self):
        """
        A configuration with sections and items that are not matched by the static
        validation but are matched by the dynamic validation is considered
        valid.
        """
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\n')

        config = configutil.get_config(fname)
        # should succeed, no exceptions
        configutil.validate_config(
            fname,
            config,
            self.dynamic_valid_config,
        )

    def test_config_validation_invalid_item(self):
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\ninvalid = foo\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.static_valid_config,
        )
        self.assertIn("section [node] contains unknown option 'invalid'", str(e))

    def test_config_validation_invalid_section(self):
        """
        A configuration with a section that is matched by neither the static nor
        dynamic validators is rejected.
        """
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\n[invalid]\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.static_valid_config,
        )
        self.assertIn("contains unknown section [invalid]", str(e))

    def test_config_dynamic_validation_invalid_section(self):
        """
        A configuration with a section that is matched by neither the static nor
        dynamic validators is rejected.
        """
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\n[invalid]\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.dynamic_valid_config,
        )
        self.assertIn("contains unknown section [invalid]", str(e))

    def test_config_dynamic_validation_invalid_item(self):
        """
        A configuration with a section, item pair that is matched by neither the
        static nor dynamic validators is rejected.
        """
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            f.write('[node]\nvalid = foo\ninvalid = foo\n')

        config = configutil.get_config(fname)
        e = self.assertRaises(
            configutil.UnknownConfigError,
            configutil.validate_config,
            fname, config,
            self.dynamic_valid_config,
        )
        self.assertIn("section [node] contains unknown option 'invalid'", str(e))

    def test_create_client_config(self):
        d = self.mktemp()
        os.mkdir(d)
        fname = os.path.join(d, 'tahoe.cfg')

        with open(fname, 'w') as f:
            opts = {"nickname": "nick",
                    "webport": "tcp:3456",
                    "hide-ip": False,
                    "listen": "none",
                    "shares-needed": "1",
                    "shares-happy": "1",
                    "shares-total": "1",
                    }
            create_node.write_node_config(f, opts)
            create_node.write_client_config(f, opts)

        config = configutil.get_config(fname)
        # should succeed, no exceptions
        configutil.validate_config(
            fname,
            config,
            client._valid_config(),
        )
