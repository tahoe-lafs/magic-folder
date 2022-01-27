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

from .util.encoding import load_yaml
from .util.capabilities import (
    Capability,
)

from .config import (
    create_global_configuration,
)
from .snapshot import (
    create_local_author,
)
from .endpoints import (
    server_endpoint_str_to_client,
)


def magic_folder_migrate(config_dir, listen_endpoint_str, tahoe_node_directory, author_name,
                         client_endpoint_str):
    """
    From an existing Tahoe-LAFS 1.14.0 or earlier configuration we
    initialize a new magic-folder using the relevant configuration
    found there. This cannot invent a listening-endpoint (hence one
    must be passed here).

    :param FilePath config_dir: a non-existant directory in which to put configuration

    :param unicode listen_endpoint_str: a Twisted server-string where we
        will listen for REST API requests (e.g. "tcp:1234")

    :param FilePath tahoe_node_directory: existing Tahoe-LAFS
        node-directory with at least one configured magic folder.

    :param unicode author_name: the name of our author (will be used
        for each magic-folder we create from the "other" config)

    :param unicode client_endpoint_str: Twisted client-string to our API
        (or None to autoconvert the listen_endpoint)

    :return Deferred[GlobalConfigDatabase]: the newly migrated
        configuration or an exception upon error.
    """

    if client_endpoint_str is None:
        client_endpoint_str = server_endpoint_str_to_client(listen_endpoint_str)

    config = create_global_configuration(
        config_dir,
        listen_endpoint_str,
        tahoe_node_directory,
        client_endpoint_str,
    )

    # now that we have the global configuration we find all the
    # configured magic-folders and migrate them.
    magic_folders = load_yaml(
        tahoe_node_directory.child("private").child("magic_folders.yaml").open("r"),
    )
    for mf_name, mf_config in magic_folders['magic-folders'].items():
        author = create_local_author(author_name)

        config.create_magic_folder(
            mf_name,
            FilePath(mf_config[u'directory']),
            author,
            Capability.from_string(mf_config[u'collective_dircap']),
            Capability.from_string(mf_config[u'upload_dircap']),
            int(mf_config[u'poll_interval']),  # is this always available?
            # tahoe-lafs's magic-folder implementation didn't have scan-interval
            # so use poll-interval for it as well.
            int(mf_config[u'poll_interval']),
        )

    return succeed(config)
