# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functions and types that implement snapshots
"""
from __future__ import print_function

import time
import json
import attr

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

from .common import (
    get_node_url,
)

from twisted.web.client import (
    readBody,
)

from twisted.web.http import (
    OK,
    CREATED,
)

from hyperlink import (
    DecodedURL,
)

from .common import (
    bad_response,
)

from eliot import (
    start_action,
    register_exception_extractor,
)

# version of the snapshot scheme
SNAPSHOT_VERSION = 1

class TahoeWriteException(Exception):
    """
    Something went wrong while doing a `tahoe put`.
    """
    def __init__(self, code, body):
        self.code = code
        self.body = body

    def __str__(self):
        return '<TahoeWriteException code={} body="{}">'.format(
            self.code,
            self.body,
        )


# log exception caused while doing a tahoe put API
register_exception_extractor(TahoeWriteException, lambda e: {"code": e.code, "body": e.body })

@inlineCallbacks
def tahoe_put_immutable(nodeurl, filepath, treq):
    """
    :param DecodedURL nodeurl: The web endpoint of the Tahoe-LAFS client
        associated with the magic-folder client.
    :param FilePath filepath: The file path that needs to be stored into
        the grid.
    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to
        make the queries.
    :return Deferred[unicode]: The readcap associated with the newly created
        unlinked file.
    """
    url = nodeurl.child(
        u"uri",
    ).add(
        u"format",
        u"CHK",
    )

    with start_action(
            action_type=u"magic_folder:tahoe_snapshot:tahoe_put_immutable"):
        put_uri = url.to_uri().to_text().encode("ascii")
        # XXX: Should not read entire file into memory. See:
        # https://github.com/LeastAuthority/magic-folder/issues/129
        with filepath.open("r") as file:
            data = file.read()

        # XXX the treq.testing stuff doesn't appear to work with put() ...
        # response = yield treq.put(put_uri, data)
        response = yield treq.get(put_uri)
        if response.code == OK or response.code == CREATED:
            result = yield readBody(response)
            returnValue(result)
        else:
            body = yield readBody(response)
            raise TahoeWriteException(response.code, body)


@inlineCallbacks
def tahoe_create_snapshot_dir(nodeurl, content, parents, timestamp, treq):
    """
    :param DecodedURL nodeurl: The web endpoint of the Tahoe-LAFS client
        associated with the magic-folder client.
    :param unicode content: readcap for the content.
    :param [unicode] parents: List of parent snapshot caps
    :param integer timestamp: POSIX timestamp that represents the creation time
    :return Deferred[unicode]: The readcap associated with the newly created
        snapshot.
    """

    # XXX do we want lists in here, or would tuples be better?
    # dict that would be serialized to JSON

    # XXX note, I changed this from the previous format, which looked .. odd.
    body = {
        u"content": {
            u"ro_uri": content,
            u"metadata": {}
        },
        u"version": SNAPSHOT_VERSION,
        u"timestamp": timestamp,
    }

    action = start_action(
        action_type=u"magic_folder:tahoe_snapshot:tahoe_create_snapshot_dir",
    )
    with action:
        # populate parents
        # The goal is to populate the dictionary with keys u"parent0", u"parent1" ...
        # with corresponding dirnode values that point to the parent URIs.
        if parents != []:
            for (i, parent) in enumerate(parents):
                body[u"parent{}".format(i)] = ["dirnode", {u"ro_uri": parent}]

        body_json = json.dumps(body, indent=4)

        # POST /uri?t=mkdir-immutable
        url = nodeurl.child(
            u"uri",
        ).add(
            u"t",
            u"mkdir-immutable"
        )

        post_uri = url.to_uri().to_text().encode("ascii")
        # XXX I can't get the treq.testing stuff to work with .post() yet
        # response = yield treq.post(post_uri, body_json)
        response = yield treq.get(post_uri)
        if response.code != OK:
            returnValue((yield bad_response(url, response)))

        result = yield readBody(response)
        returnValue(result)

@attr.s
class TahoeSnapshot(object):
    """
    Represents a snapshot corresponding to a file.

    XXX we want a 'file name' of some sort .. is that relative to the
    magic-folder base, or absolute, or that weird 'flattened' thing
    magic-folder does?
    """

    capability = attr.ib()
    parents = attr.ib()


@inlineCallbacks
def create_snapshot(node_directory, filepath, parents, treq):
    """
    Create a snapshot.

    :param [unicode] parents: List of parent snapshots of the current snapshot
        (read-caps of parent snapshots)

    :param HTTPClient treq: An ``HTTPClient`` or similar object to use to
        make the queries.

    :return Deferred[unicode]: Snapshot read-only cap is returned on success.
        Otherwise an appropriate exception is raised.
    """

    # XXX check 'parents': it should be a sequence of capabilities of
    # snapshots.


    # XXX get rid of 'node_directory': if we need a whole Tahoe
    # config, pass one. If we only need the node-uri, pass that
    # instead.

    action = start_action(
        action_type=u"magic_folder:tahoe_snapshot:create_snapshot",
    )
    with action:
        # XXX this seems .. complex. And also 'unicode' is python2-only
        nodeurl_u = unicode(get_node_url(node_directory.asBytesMode().path), 'utf-8')
        nodeurl = DecodedURL.from_text(nodeurl_u)

        content_cap = yield tahoe_put_immutable(nodeurl, filepath, treq)

        # XXX probably want a reactor/clock passed in?
        now = time.time()

        # HTTP POST mkdir-immutable
        snapshot_cap = yield tahoe_create_snapshot_dir(
            nodeurl,
            content_cap,
            parents,
            now,
            treq,
        )

        returnValue(
            TahoeSnapshot(
                snapshot_cap,
                parents,
            )
        )
