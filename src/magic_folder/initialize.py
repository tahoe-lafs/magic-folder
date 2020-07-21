# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder init command.
"""

from twisted.internet.defer import (
    succeed,
)

from .config import (
    create_global_configuration,
)


def magic_folder_initialize(config_dir, listen_endpoint, tahoe_node_directory):
    """
    Initialize a magic-folder daemon configuration with the specified required options in ``config_dir``.

    :param FilePath config_dir: a non-existant directory in which to put configuration

    :param unicode listen_endpoint: a Twisted server-string where we
        will listen for REST API requests (e.g. "tcp:1234")

    :param FilePath tahoe_node_directory: the directory containing our
        Tahoe-LAFS client's configuration

    :return Deferred[integer]: A status code of 0 for a successful execution. Otherwise
        an appropriate exception is raised.
    """

    create_global_configuration(
        config_dir,
        listen_endpoint,
        tahoe_node_directory,
    )

    return succeed(0)
