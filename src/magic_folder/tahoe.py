# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Interfaces with Tahoe-LAFS via the Web API
"""
import codecs
import os
import json
import re

from allmydata.util import fileutil
from allmydata.scripts.common_http import format_http_error
from allmydata.scripts.common import (
    get_aliases,
    get_alias,
    DEFAULT_ALIAS,
    escape_path,
)
from allmydata.util.encodingutil import (
    quote_output,
    to_str,
)
from allmydata import uri
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from hyperlink import (
    DecodedURL,
)

from twisted.web.http import (
    OK,
    CONFLICT,
)

from twisted.web.client import (
    readBody,
)

from .common import (
    bad_response,
)

# Until there is a web API, we replicate add_alias.
# https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3286
def add_line_to_aliasfile(aliasfile, alias, cap):
    # we use os.path.exists, rather than catching EnvironmentError, to avoid
    # clobbering the valuable alias file in case of spurious or transient
    # filesystem errors.
    if os.path.exists(aliasfile):
        with codecs.open(aliasfile, "r", "utf-8") as f:
            aliases = f.read()
        if not aliases.endswith("\n"):
            aliases += "\n"
    else:
        aliases = ""
    aliases += "%s: %s\n" % (alias, cap)
    with codecs.open(aliasfile+".tmp", "w", "utf-8") as f:
        f.write(aliases)
    fileutil.move_into_place(aliasfile+".tmp", aliasfile)

def _add_alias(node_directory, alias, cap):
    if u":" in alias:
        raise Exception("Alias names cannot contain colons.")
    if u" " in alias:
        raise Exception("Alias names cannot contain spaces.")

    old_aliases = get_aliases(node_directory)
    if alias in old_aliases:
        raise Exception("Alias {} already exists!".format(quote_output(alias)))

    aliasfile = os.path.join(node_directory, "private", "aliases")
    cap = uri.from_string_dirnode(cap).to_string()

    add_line_to_aliasfile(aliasfile, alias, cap)

    return 0

@inlineCallbacks
def tahoe_create_alias(node_directory, alias, treq):
    # mkdir+add_alias

    nodeurl = get_node_url(node_directory)

    try:
        node_url = DecodedURL.from_text(unicode(nodeurl, 'utf-8'))
        new_uri = yield tahoe_mkdir(node_url, treq)
    except Exception:
        raise

    _add_alias(node_directory, alias, new_uri)

    returnValue(0)

def get_node_url(node_directory):
    node_url_file = os.path.join(node_directory, u"node.url")
    node_url = fileutil.read(node_url_file).strip()

    return node_url

@inlineCallbacks
def tahoe_mkdir(nodeurl, treq):
    """
    :param DecodedURL nodeurl: The web endpoint of the Tahoe-LAFS client
        associated with the magic-wormhole client.

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to
        make the queries.

    :return Deferred[unicode]: The writecap associated with the newly created unlinked
        directory.
    """
    url = nodeurl.child(
        u"uri",
    ).add(
        u"t",
        u"mkdir",
    )

    post_uri = url.to_uri().to_text().encode("ascii")
    response = yield treq.post(post_uri)
    if response.code != OK:
        returnValue((yield bad_response(url, response)))

    result = yield readBody(response)
    # emit its write-cap
    returnValue(result)

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

