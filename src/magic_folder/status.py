# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Present status information about magic folders.
"""

from io import (
    BytesIO,
)

from json import (
    loads,
)

import attr

from eliot.twisted import (
    inline_callbacks,
)

from twisted.internet.defer import (
    returnValue,
)

from .frontends.magic_folder import (
    load_magic_folders,
)

from hyperlink import (
    DecodedURL,
)

from twisted.web.http import (
    OK,
)
from twisted.web.client import (
    FileBodyProducer,
    readBody,
)

class BadFolderName(Exception):
    """
    An operation was attempted on a magic folder using a name which does not
    exist.
    """
    def __str__(self):
        return "Folder named {!r} not found.".format(name)


class BadResponseCode(Exception):
    """
    An HTTP request received a response code which does not allow an operation
    to progress further.
    """
    def __str__(self):
        return "Request for {!r} received unexpected response code {!r}:\n{}".format(
            self.args[0].to_text(),
            self.args[1],
            self.args[2],
        )

class BadMetadataResponse(Exception):
    """
    An attempt to load some metadata about something received a response that
    cannot be interpreted as that metadata.
    """


class BadDirectoryCapability(Exception):
    """
    A capability which was expected to refer to a directory object instead did
    not.
    """


@attr.s
class Status(object):
    """
    Information about a magic folder at a point in time.
    """
    folder_name = attr.ib()
    local_files = attr.ib()
    remote_files = attr.ib()
    folder_status = attr.ib()


def status(folder_name, node_directory, treq):
    """
    Retrieve information about the current state of a named magic folder.

    :param unicode folder_name: The name of the magic folder about which to
        return information.

    :param FilePath node_directory: The path to the Tahoe-LAFS node directory
        which owns the magic folder in question.

    :return Deferred[Status]: A Deferred which fires with information about
        the magic folder.
    """
    magic_folders = load_magic_folders(node_directory.path)
    token = node_directory.descendant([u"private", u"api_auth_token"]).getContent()
    node_root_url = DecodedURL.from_text(
        node_directory.child(u"node.url").getContent().decode("ascii").strip(),
    )
    magic_root_url = DecodedURL.from_text(u"http://127.0.0.1:9889/")

    try:
        folder_config = magic_folders[folder_name]
    except KeyError:
        raise BadFolderName(node_directory, folder_name)

    return status_from_folder_config(
        folder_name,
        folder_config[u"upload_dircap"].decode("ascii"),
        folder_config[u"collective_dircap"].decode("ascii"),
        node_root_url,
        magic_root_url,
        token,
        treq,
    )


@inline_callbacks
def status_from_folder_config(
        folder_name,
        upload_dircap,
        collective_dircap,
        node_url,
        magic_url,
        token,
        treq,
):
    """
    Retrieve information about the current state of a magic folder given its
    configuration.

    :return Deferred[Status]: Details about the current state of the named
        magic folder.
    """
    dmd_stat = yield _cap_metadata(treq, node_url, upload_dircap)
    collective_stat = yield _cap_metadata(treq, node_url, collective_dircap)
    folder_status = yield magic_folder_status(
        folder_name,
        magic_url,
        token,
        treq,
    )

    def dirnode_cap(cap_stat):
        captype, metadata = cap_stat
        if captype != u"dirnode":
            raise BadDirectoryCapability(captype)
        return metadata

    local_files = dirnode_cap(dmd_stat)[u"children"]
    remote_files = {}
    collective_meta = dirnode_cap(collective_stat)
    collective_children = collective_meta[u"children"]
    for (dmd_name, dmd_meta) in collective_children.items():
        remote_files[dmd_name] = dirnode_cap((
            yield _cap_metadata(treq, node_url, dirnode_cap(dmd_meta)[u"ro_uri"])
        ))[u"children"]

    returnValue(Status(
        folder_name=folder_name,
        local_files=local_files,
        remote_files=remote_files,
        folder_status=folder_status,
    ))


@inline_callbacks
def magic_folder_status(folder_name, root_url, token, treq):
    body = u"token={}".format(token).encode("ascii")
    url = root_url.child(
        u"api",
    ).add(
        u"t",
        u"json",
    ).add(
        u"name",
        folder_name,
    )
    response = yield treq.post(
        url.to_uri().to_text().encode("ascii"),
        body,
    )
    if response.code != OK:
        returnValue((yield bad_response(url, response)))

    result = _check_result(loads((yield readBody(response))))
    returnValue(result)


def _get(treq, url):
    return treq.get(
        url.to_uri().to_text().encode("ascii"),
    )


@inline_callbacks
def _cap_metadata(treq, root_url, cap):
    """
    Retrieve metadata about the object reachable via a capability.

    :param treq.HTTPClient treq:
    :param hyperlink.DecodedURL root_url:
    :param unicode cap:

    :return Deferred[something]:
    """
    url = root_url.child(u"uri", cap).add(u"t", u"json")
    response = yield _get(treq, url)
    if response.code != OK:
        returnValue((yield bad_response(url, response)))
    result = _check_result(loads((yield readBody(response))))
    if len(result) != 2:
        raise BadMetadataResponse(result)
    returnValue(result)

@inline_callbacks
def bad_response(url, response):
    body = yield readBody(response)
    raise BadResponseCode(url, response.code, body)


def _check_result(result):
    if isinstance(result, dict) and u"error" in result:
        raise BadMetadataResponse(result)
    return result
