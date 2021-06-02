"""
Utilities related to Twisted endpoint (and endpoint-strings)
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)

from twisted.internet.endpoints import (
    _parse as twisted_endpoint_parse,
)


class CannotConvertEndpointError(Exception):
    """
    Failed to convert a server endpoint-string into a corresponding
    client one.
    """


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
    return u"tcp:{}:{}".format(host, port)


def _unix_endpoint_to_client(args, kwargs):
    """
    convert a 'unix:' server endpoint-string to client
    """
    address = args[0]
    return u"unix:{}".format(address)
