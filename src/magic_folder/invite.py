# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements the magic-folder invite command.
"""

import re
import json

from allmydata.scripts.common import (
    get_aliases,
    get_alias,
    DEFAULT_ALIAS,
    escape_path,
)
from allmydata.scripts.common_http import format_http_error

from allmydata import uri
from allmydata.util.encodingutil import to_str

from twisted.internet.defer import (
    returnValue,
    inlineCallbacks
)

from hyperlink import (
    DecodedURL,
)

from twisted.web.client import (
    readBody,
)

from twisted.web.http import (
    OK,
    CONFLICT,
)

from .common import (
    bad_response,
    get_node_url,
    tahoe_mkdir,
    INVITE_SEPARATOR,
)

@inlineCallbacks
def tahoe_mv(nodeurl, aliases, from_file, to_file, treq):
    """
    :param DecodedURL nodeurl: The web end point of the Tahoe-LAFS node associated
        with the magic-folder client.
    :param [unicode] aliases: XXX

    :param unicode from_file: cap of the source.

    :param unicode to_file: cap of the destination.

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return integer: Returns 0 for successful execution. In the case of a failure,
        an exception is raised.
    """
    try:
        rootcap, from_path = get_alias(aliases, from_file, DEFAULT_ALIAS)
    except Exception as e:
        raise e

    from_url = nodeurl.child(
        u"uri",
        u"{}".format(rootcap),
    )

    if from_path:
        from_url = from_url.child(
            u"{}".format(escape_path(from_path))
        )

    from_url = from_url.add(
        u"t",
        u"json",
    )

    get_url = from_url.to_uri().to_text().encode("ascii")
    response = yield treq.get(get_url)
    if response.code != OK:
        returnValue((yield bad_response(get_url, response)))
    result = yield readBody(response)

    nodetype, attrs = json.loads(result)
    cap = to_str(attrs.get("rw_uri") or attrs["ro_uri"])

    # now get the target
    try:
        rootcap, path = get_alias(aliases, to_file, DEFAULT_ALIAS)
    except Exception as e:
        raise e

    to_url = nodeurl.child(
        u"uri",
        u"{}".format(rootcap)
    )

    if path:
        to_url = to_url.child(
            u"{}".format(escape_path(path))
        )

    if path.endswith("/"):
        # "mv foo.txt bar/" == "mv foo.txt bar/foo.txt"
        to_url = to_url.child(
            u"{}".format(escape_path(from_path[from_path.rfind("/")+1:]))
        )

    put_url = to_url.add(
        u"t",
        u"uri",
    ).add(
        u"replace",
        u"only-files",
    )

    response = yield treq.put(put_url.to_text(), cap)
    if not re.search(r'^2\d\d$', str(response.code)):
        if response.code == CONFLICT:
            raise Exception("You cannot overwrite a directory with a file")
        else:
            raise Exception(format_http_error("Error", response))

    returnValue(0)


@inlineCallbacks
def magic_folder_invite(config, folder_name, invitee_name, treq):
    """
    Invite a user identified by the nickname to a folder owned by the alias

    :param GlobalConfigDatabase config: our configuration

    :param unicode folder_name: The name of an existing magic-folder

    :param unicode invitee_name: The nickname for the new participant

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to make
        the queries.

    :return Deferred[unicode]: A secret invitation code.
    """

    # get configuration for this magic-folder (or error if it doesn't
    # exist)
    try:
        folder_config = config.get_magic_folder(folder_name)
    except ValueError:
        raise usage.UsageError(
            u"No magic-folder named '{}'".format(folder_name)
        )

    # create an unlinked directory and get the dmd write-cap
    dmd_write_cap = yield tahoe_mkdir(config.tahoe_client_url, treq)

    # derive a dmd read-only cap from it.
    dmd_readonly_cap = uri.from_string(dmd_write_cap.encode("utf8")).get_readonly().to_string()
    if dmd_readonly_cap is None:
        raise Exception("failed to diminish dmd write cap")

    # Now, we need to create a link to the nickname from inside the
    # collective to this read-cap. For that we will need to know
    # the write-cap of the collective (which is stored by the private/aliases
    # file in the node_directory) so that a link can be created inside it
    # to the .
    # To do that, we use tahoe ln dmd_read_cap <collective-write-cap>/<alias>

    magic_write_cap = folder_config.collective_dircap
    magic_readonly_cap = uri.from_string(magic_write_cap.encode("utf8")).get_readonly().to_string()

    # tahoe ln CLIENT_READCAP COLLECTIVE_WRITECAP/NICKNAME
    #from_file = dmd_readonly_cap.decode('utf-8')
    #to_file = u"{}/{}".format(magic_write_cap.decode('utf-8'), nickname)

    # /uri/<root-cap>/<path>?t=uri&replace=only-files
    # XXX
    put_url = config.tahoe_client_url.child(u"uri")
    put_url = put_url.child(magic_write_cap.decode("utf8"))
    put_url = put_url.child(invitee_name)
    put_url = put_url.add(u"t", u"uri")
    put_url = put_url.add(u"replace", u"only-files")
    resp = yield treq.put(put_url.to_text(), dmd_readonly_cap)
    if resp.code < 200 or resp.code >= 300:
        if resp.code == CONFLICT:
            raise Exception(
                "You cannot overwrite a directory with a file"
                " (most likely participant '{}' already exists)".format(invitee_name)
            )
        else:
            raise Exception(format_http_error("Error", resp))

    invite_code = "{}{}{}".format(
        magic_readonly_cap,
        INVITE_SEPARATOR,
        dmd_write_cap,
    )

    returnValue(invite_code)
