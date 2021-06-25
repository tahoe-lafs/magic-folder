# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder create command.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals
)

from twisted.internet.defer import (
    inlineCallbacks,
)
from twisted.web import http

from allmydata.uri import (
    from_string as tahoe_uri_from_string,
)

from .common import APIError
from .snapshot import (
    create_local_author,
)


@inlineCallbacks
def magic_folder_create(config, name, author_name, local_dir, poll_interval, scan_interval, tahoe_client):
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

    :param integer scan_interval: Every 'scan_interval' seconds the
        local directory will be scanned for changes.

    :param TahoeClient tahoe_client: The client we use to make queries

    :return Deferred: ``None`` or an appropriate exception is raised.
    """

    if name in config.list_magic_folders():
        raise APIError(
            code=http.CONFLICT,
            reason="Already have a magic-folder named '{}'".format(name),
        )

    if scan_interval < 0:
        raise APIError(
            code=http.NOT_ACCEPTABLE,
            reason="scan_interval must be >= 0",
        )

    # create our author
    author = create_local_author(author_name)

    # create an unlinked directory and get the dmd write-cap
    collective_write_cap = yield tahoe_client.create_mutable_directory()

    # create the personal dmd write-cap
    personal_write_cap = yield tahoe_client.create_mutable_directory()

    # 'attenuate' our personal dmd write-cap to a read-cap
    personal_readonly_cap = tahoe_uri_from_string(personal_write_cap).get_readonly().to_string().encode("ascii")

    # add ourselves to the collective
    yield tahoe_client.add_entry_to_mutable_directory(
        mutable_cap=collective_write_cap,
        path_name=author_name,
        entry_cap=personal_readonly_cap,
    )

    config.create_magic_folder(
        name,
        local_dir,
        author,
        collective_write_cap,
        personal_write_cap,
        poll_interval,
        scan_interval,
    )
