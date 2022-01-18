"""
Tests for ``magic_folder.endpoints``.
"""

from testtools.matchers import (
    Equals,
)
from twisted.internet.address import (
    IPv4Address,
    IPv6Address,
)

from .common import (
    SyncTestCase,
)
from ..endpoints import (
    client_endpoint_from_address,
)


class AddressTests(SyncTestCase):
    """
    Address parsing and serializing
    """

    def test_v4_address(self):
        """
        An IPv4Address is converted properly
        """
        addr = IPv4Address("TCP", "127.0.0.1", 1234)
        description = client_endpoint_from_address(addr)
        self.assertThat(
            description,
            Equals("tcp:127.0.0.1:1234")
        )

    def test_v6_address(self):
        """
        An IPv6Address is converted properly
        """
        addr = IPv6Address("TCP", "::1", 1234)
        description = client_endpoint_from_address(addr)
        self.assertThat(
            description,
            Equals(r"tcp:\:\:1:1234")
        )

    def test_udp_address(self):
        """
        An IPv4Address that is UDP is not converted
        """
        addr = IPv4Address("UDP", "127.0.0.1", 1234)
        description = client_endpoint_from_address(addr)
        self.assertThat(
            description,
            Equals(None)
        )
