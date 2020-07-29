# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder create command.
"""

from twisted.internet.defer import (
    inlineCallbacks,
)

from .snapshot import (
    create_local_author,
)
from .common import (
    BadResponseCode,
)
from .tahoe_client import (
    create_tahoe_client,
)


@inlineCallbacks
def magic_folder_create(config, name, author_name, local_dir, poll_interval, treq):
    """
    Create a magic-folder with the specified ``name`` and
    ``local_dir``.

    :param GlobalConfigDatabase config: Our configuration

    :param unicode name: The name of the magic-folder.

    :param unicode author_name: The name for our author

    :param FilePath local_dir: The directory on the filesystem that the user wants
        to sync between different computers.

    :param integer poll_interval: Periodic time interval after which the
        client polls for updates.

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred: ``None`` or an appropriate exception is raised.
    """

    # XXX probably want to pass this in, instead of "treq"?
    tahoe_client = create_tahoe_client(config.tahoe_client_url, treq)

    if name in config.list_magic_folders():
        raise Exception("Already have a magic-folder named '{}'".format(name))

    # create our author
    author = create_local_author(author_name)

    # create an unlinked directory and get the dmd write-cap
    try:
        collective_write_cap = yield tahoe_client.create_mutable_directory()
    except BadResponseCode as e:
        raise RuntimeError(
            "Error from '{}' while creating mutable directory: {}".format(
                config.tahoe_client_url,
                e
            )
        )

    try:
        personal_write_cap = yield tahoe_client.create_mutable_directory()
    except BadResponseCode as e:
        raise RuntimeError(
            "Error from '{}' while creating mutable directory: {}".format(
                config.tahoe_client_url,
                e
            )
        )

    # add ourselves to the collective
    try:
        yield tahoe_client.add_entry_to_mutable_directory(
            mutable_cap=collective_write_cap,
            path_name=author_name,
            entry_cap=personal_write_cap,
        )
    except BadResponseCode as e:
        raise RuntimeError(
            "Error adding '{}' to collective directory: {}".format(
                author_name,
                e
            )
        )


    # create our "state" directory for this magic-folder (could be
    # configurable in the future)
    state_dir = config.get_default_state_path(name)
    try:
        config.create_magic_folder(
            name,
            local_dir,
            state_dir,
            author,
            collective_write_cap,
            personal_write_cap,
            poll_interval,
        )
    except Exception:
        state_dir.remove()
        raise
