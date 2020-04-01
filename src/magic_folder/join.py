# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder join command.
"""
import os

from twisted.python import usage

from .frontends.magic_folder import (
    load_magic_folders,
    maybe_upgrade_magic_folders,
    save_magic_folders,
)

from .common import (
    INVITE_SEPARATOR
)

def magic_folder_join(invite_code, node_directory, local_dir, name, poll_interval):
    """
    Join a magic-folder specified by the ``name`` and create the config files.
    """
    fields = invite_code.split(INVITE_SEPARATOR)
    if len(fields) != 2:
        raise usage.UsageError("Invalid invite code.")
    magic_readonly_cap, dmd_write_cap = fields

    maybe_upgrade_magic_folders(node_directory)
    existing_folders = load_magic_folders(node_directory)

    if name in existing_folders:
        raise Exception("This client already has a magic-folder named '{}'".format(name))

    db_fname = os.path.join(
        node_directory,
        u"private",
        u"magicfolder_{}.sqlite".format(name),
    )
    if os.path.exists(db_fname):
        raise Exception("Database '{}' already exists; not overwriting".format(db_fname))

    folder = {
        u"directory": local_dir.encode('utf-8'),
        u"collective_dircap": magic_readonly_cap,
        u"upload_dircap": dmd_write_cap,
        u"poll_interval": poll_interval,
    }
    existing_folders[name] = folder

    save_magic_folders(node_directory, existing_folders)
    return 0
