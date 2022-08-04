# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Turn Twisted endpoint description strings into HTTP(S) URLs.
"""

from hyperlink import (
    URL,
)

def endpoint_description_to_http_api_root(endpoint_description):
    """
    Parse a Twisted endpoint description string and return a ``DecodedURL`` an
    HTTP client could use to talk to an HTTP server listening on that
    endpoint.

    This currently supports only **tcp** and **ssl** endpoints.

    :param str endpoint_description: The endpoint description string.

    :return DecodedURL: A URL for reaching the given endpoint.
    """
    parts = endpoint_description.split(u":")
    return _ENDPOINT_CONVERTERS[parts[0].lower()](parts[1:])


def _tcp_to_http_api_root(parts):
    """
    Construct an HTTP URL.
    """
    port, host = _get_tcpish_parts(parts)
    return URL(
        scheme=u"http",
        host=host,
        port=port,
    ).get_decoded_url()


def _ssl_to_https_api_root(parts):
    """
    Construct an HTTPS URL.
    """
    port, host = _get_tcpish_parts(parts)
    return URL(
        scheme=u"https",
        host=host,
        port=port,
    ).get_decoded_url()


def _get_tcpish_parts(parts):
    """
    Split up the details of an endpoint with a host and port number (like
    **tcp** and **ssl**).
    """
    port_number = int(parts.pop(0))
    kwargs = dict(part.split(u"=", 1) for part in parts)
    interface = kwargs.get(u"interface", u"127.0.0.1")
    if interface == u"0.0.0.0":
        interface = u"127.0.0.1"
    return port_number, interface


_ENDPOINT_CONVERTERS = {
    u"tcp": _tcp_to_http_api_root,
    u"ssl": _ssl_to_https_api_root,
}
