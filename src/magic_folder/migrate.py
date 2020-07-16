# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the 'magic-folder migrate' command.
"""

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    succeed,
)

import yaml

from .config import (
    create_global_configuration,
)
from .snapshot import (
    create_local_author,
)


def magic_folder_migrate(config_dir, listen_endpoint, tahoe_node_directory, author_name):
    """
    From an existing Tahoe-LAFS 1.14.0 or earlier configuration we
    initialize a new magic-folder using the relevant configuration
    found there. This cannot invent a listening-endpoint (hence one
    must be passed here).

    :param FilePath config_dir: a non-existant directory in which to put configuration

    :param unicode listen_endpoint: a Twisted server-string where we
        will listen for REST API requests (e.g. "tcp:1234")

    :param FilePath tahoe_node_directory: existing Tahoe-LAFS
        node-directory with at least one configured magic folder.

    :param unicode author_name: the name of our author (will be used
        for each magic-folder we create from the "other" config)

    :return Deferred[GlobalConfigDatabase]: the newly migrated
        configuration or an exception upon error.
    """

    # Extract interesting Tahoe configuration. To create the global
    # configuration, all we need is the tahoe WebUI API
    with tahoe_node_directory.child("node.url").open("r") as f:
        tahoe_url = f.read().strip()

    config = create_global_configuration(
        config_dir,
        listen_endpoint,
        tahoe_url,
    )


    # now that we have the global configuration we find all the
    # configured magic-folders and migrate them.
    magic_folders = yaml.safe_load(
        tahoe_node_directory.child("private").child("magic_folders.yaml").open("r"),
    )
    for mf_name, mf_config in magic_folders['magic-folders'].items():
        state_dir = config_dir.child(mf_name)
        author = create_local_author(author_name)

        mf = config.create_magic_folder(
            mf_name,
            FilePath(mf_config[u'directory']),
            state_dir,
            author,
            mf_config[u'collective_dircap'],
            mf_config[u'upload_dircap'],
            mf_config[u'poll_interval'],  # is this always available?
        )

    return succeed(config)
