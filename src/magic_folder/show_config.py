# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder init command.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)

import sys
from json import (
    dumps,
)

from nacl.encoding import (
    Base32Encoder,
)

from twisted.internet.defer import (
    succeed,
)


def magic_folder_show_config(config, stdout=None):
    """
    Dump configuration as JSON.

    :param GlobalConfigDatabase config: a magic-folder config directory
    """

    def folder_json(mf):
        return {
            "name": mf.name,
            "author_name": mf.author.name,
            "author_private_key": mf.author.verify_key.encode(Base32Encoder),
            "stash_path": mf.stash_path.path,
        }

    magic_folders = {
        name: folder_json(config.get_magic_folder(name))
        for name in config.list_magic_folders()
    }
    json = {
        "tahoe_node_directory": config.tahoe_node_directory.path,
        "api_endpoint": config.api_endpoint,
        "api_token": config.api_token,
        "magic_folders": magic_folders,
    }

    if stdout is None:
        stdout = sys.stdout
    print(dumps(json, indent=4).decode("utf8"), file=stdout)
    return succeed(0)
