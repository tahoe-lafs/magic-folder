# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.client``.
"""

from testtools.matchers import (
    Equals,
    Always,
)

from testtools.twistedsupport import (
    succeeded,
)
from treq.testing import (
    StubTreq,
    StringStubbingResource,
)

from ..client import (
    MagicFolderClient,
)
from .common import (
    SyncTestCase,
)


class MagicFolderClientTests(SyncTestCase):
    """
    Tests for MagicFolderClient
    """
    def setup_client(self):
        """
        Set up a Magic Folder API client that will simply record all the
        API calls / args / etc.
        """

        self.api_calls = []

        def get_resource_for(method, url, params, headers, data):
            self.api_calls.append((method, url, params, headers, data))
            return (200, {}, b"{}")

        self.client = MagicFolderClient(
            StubTreq(StringStubbingResource(get_resource_for)),
            lambda: b"fake token",
        )

    def setUp(self):
        super(MagicFolderClientTests, self).setUp()
        self.setup_client()

    def _client_method_request(self, method, args, req_kind, req_url):
        """
        Test that calling a given `method` results in the client making a
        request to the given `req_url` (with HTTP verb `req_kind`).
        """
        self.assertThat(
            getattr(self.client, method)(*args),
            succeeded(Always()),
        )
        self.assertThat(
            self.api_calls,
            Equals([
                (req_kind,
                 req_url,
                 {},
                 {
                     b'Accept-Encoding': [b'gzip'],
                     b'Authorization': [b'Bearer fake token'],
                     b'Connection': [b'close'],
                     b'Host': [b'invalid.'],
                 },
                 b'',
                ),
            ])
        )

    def test_tahoe_objects(self):
        """
        The /tahoe-objects API works
        """
        return self._client_method_request(
            "tahoe_objects",
            ("a_magic_folder", ),
            b"GET",
            "http://invalid./v1/magic-folder/a_magic_folder/tahoe-objects",
        )

    def test_list_invites(self):
        """
        The /list-invites API works
        """
        return self._client_method_request(
            "list_invites",
            ("folder_name", ),
            b"GET",
            "http://invalid./v1/magic-folder/folder_name/invites",
        )
