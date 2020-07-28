# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder join command.
"""
import os

from twisted.python import usage

from allmydata.client import read_config

from .magic_folder import (
    load_magic_folders,
    maybe_upgrade_magic_folders,
    save_magic_folders,
)

from .common import (
    INVITE_SEPARATOR
)
from .snapshot import (
    create_local_author,
    write_local_author,
)


def magic_folder_join(config, invite_code, local_dir, name, poll_interval, author_name):
    """
    Join a magic-folder specified by the ``name`` and create the config files.

    :param GlobalConfgDatabase config: our configuration

    :param unicode invite_code: The code used to join a magic folder.

    :param FilePath local_dir: The directory in the local filesystem that holds
        the files to be synchronized across computers.

    :param unicode name: The magic-folder name.

    :param integer poll_interval: Periodic time interval after which the
        client polls for updates.

    :param unicode author_name: Our own name for Snapshot authorship

    :return: None or exception is raised on error.
    """
    fields = invite_code.split(INVITE_SEPARATOR)
    if len(fields) != 2:
        raise usage.UsageError("Invalid invite code.")
    magic_readonly_cap, dmd_write_cap = fields

    if name in config.list_magic_folders():
        raise Exception(
            "This client already has a magic-folder named '{}'".format(name)
        )

    author = create_local_author(author_name)
    config.create_magic_folder(
        name,
        local_dir,
        config.get_default_state_path(name),
        author,
        magic_readonly_cap,
        dmd_write_cap,
        poll_interval,
    )
