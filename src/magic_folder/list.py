# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements ```magic-folder list``` command.
"""

import os
import json

from twisted.internet import reactor
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.internet.error import (
    ConnectError,
)
from twisted.internet.endpoints import (
    clientFromString,
)
from twisted.web.client import (
    Agent,
    readBody,
)
from twisted.web import http
from twisted.web.iweb import (
    IAgentEndpointFactory,
)

from hyperlink import  DecodedURL
from treq.client import HTTPClient
from allmydata.client import read_config
from zope.interface import implementer

from .config import endpoint_description_to_http_api_root



@implementer(IAgentEndpointFactory)
class EndpointStringFactory(object):
    """
    Connect to a particular client endpoint-string
    """
    def __init__(self, reactor, endpoint_str):
        self.reactor = reactor
        self.endpoint = endpoint_str

    def endpointForURI(self, uri):
        return clientFromString(self.reactor, self.endpoint)



@inlineCallbacks
def magic_folder_list(config, include_secret_information=False):
    """
    List folders associated with a node.

    :param GlobalConfigDatabase config: our configuration

    :param bool include_secret_information: include sensitive private
        information (such as long-term keys) if True (default: False).

    :return: JSON response from `GET /v1/magic-folder`.
    """
    base_url = DecodedURL.from_text(u"http://invalid./")
    from twisted.internet import reactor

    agent = Agent.usingEndpointFactory(
        reactor,
        EndpointStringFactory(reactor, config.api_client_endpoint),
    )
    api_url = base_url.child(u'v1').child(u'magic-folder')
    if include_secret_information:
        api_url = api_url.replace(query=[(u"include_secret_information", u"1")])
    headers = {
        b"Authorization": u"Bearer {}".format(config.api_token).encode("ascii"),
    }

    # all this "access the HTTP API" stuff should be rolled into a
    # class that all the subcommands can re-use .. just keeping it all
    # here for now
    class CannotAccessApiError(Exception):
        """
        The Magic Folder HTTP API can't be reached at all
        """

    try:
        response = yield HTTPClient(agent).get(
            api_url.to_uri().to_text().encode('ascii'),
            headers=headers,
        )
    except ConnectError:
        raise CannotAccessApiError(
            "Can't reach the magic folder daemon at all on '{}'.".format(
                config.api_client_endpoint,
            )
        )

    if response.code != http.OK:
        message = http.RESPONSES.get(response.code, b"Unknown Status")
        raise Exception(
            "Magic Folder API call received error response: {} ({})".format(
                response.code, message)
        )

    result = yield readBody(response)
    returnValue(json.loads(result))
