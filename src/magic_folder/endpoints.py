"""
Utilities related to Twisted endpoint (and endpoint-strings)
"""

import re
from typing import Optional

from twisted.internet.address import IPv4Address, IPv6Address

from twisted.internet.endpoints import (
    _parse as twisted_endpoint_parse,
)
from twisted.internet.interfaces import IAddress


class CannotConvertEndpointError(Exception):
    """
    Failed to convert a server endpoint-string into a corresponding
    client one.
    """

def _quote_endpoint_argument(s):
    # type: (str) -> str
    """
    Twisted endpoint strings cannot contain colon characters inside
    individual pieces of the endpoint string (because they're
    :-delimiated).

    :returns: `s` with all : characters replaced with backslash-:
    """
    return re.sub(
        r"[\:]",
        lambda m: r"\{}".format(m.group(0)),
        s
    )

def client_endpoint_from_address(address):
    # type: (IAddress) -> Optional[str]
    """
    Turn certain kinds of IAddress into a Twisted client-style
    endpoint string. Supports only TCP on IPv4 or IPv6.

    :returns: str like "tcp:<host>:<port>" for and `address` of
        type IPV4Address or IPv6Address. None otherwise.
    """
    if isinstance(address, (IPv4Address, IPv6Address)) and address.type == "TCP":
        return "tcp:{host}:{port}".format(
            host=_quote_endpoint_argument(address.host),
            port=address.port,
        )
    return None


def server_endpoint_str_to_client(server_ep):
    """
    Attempt to convert a Twisted server endpoint-string into the
    corresponding client-type one.

    :returns: a Twisted client endpoint-string

    :raises: CannotConvertEndpointError upon failure
    """
    # so .. we could either re-create the code that splits a Twisted
    # client/server string into pieces or:
    args, kwargs = twisted_endpoint_parse(server_ep)
    # the first arg is the "kind" of endpoint, e.g. tcp, ...
    kind = args[0]
    args = args[1:]
    converters = {
        "tcp": _tcp_endpoint_to_client,
        "unix": _unix_endpoint_to_client,
    }
    try:
        converter = converters[kind]
    except KeyError:
        raise CannotConvertEndpointError(
            "Cannot covert server endpoint of type '{}' to client".format(kind)
        )
    return converter(args, kwargs)


def _tcp_endpoint_to_client(args, kwargs):
    """
    convert a 'tcp:' server endpoint-string to client
    """
    host = kwargs.get(u"interface", None) or u"127.0.0.1"
    port = args[0]
    if port == "0":
        return None
    return u"tcp:{}:{}".format(host, port)


def _unix_endpoint_to_client(args, kwargs):
    """
    convert a 'unix:' server endpoint-string to client
    """
    address = args[0]
    return u"unix:{}".format(address)
