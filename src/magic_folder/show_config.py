# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder init command.
"""

from json import (
    dumps,
)

from nacl.encoding import (
    Base32Encoder,
)

from twisted.internet.defer import (
    succeed,
)

from .config import (
    load_global_configuration,
)


def magic_folder_show_config(config_dir):
    """
    Dump configuration as JSON.

    :param FilePath config_dir: an existing magic-folder config directory
    """

    config = load_global_configuration(config_dir)

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

    print(dumps(json, indent=4))
    return succeed(0)
