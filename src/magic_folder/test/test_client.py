# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.client``.
"""

import json
from testtools.matchers import (
    Equals,
    Always,
)

from twisted.python.filepath import (
    FilePath,
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

    def _client_method_request(self, method, args, req_kind, req_url,
                               body=b"", extra_headers={}, expected_query_args={}):
        """
        Test that calling a given `method` results in the client making a
        request to the given `req_url` (with HTTP verb `req_kind`).
        """
        self.assertThat(
            getattr(self.client, method.replace("-", "_"))(*args),
            succeeded(Always()),
        )
        headers = {
            b'Accept-Encoding': [b'gzip'],
            b'Authorization': [b'Bearer fake token'],
            b'Connection': [b'close'],
            b'Host': [b'invalid.'],
        }
        headers.update(extra_headers)

        self.assertThat(
            self.api_calls,
            Equals([
                (req_kind,
                 req_url,
                 expected_query_args,
                 headers,
                 body,
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

    def test_create_invite(self):
        """
        The /create-invite API works
        """
        return self._client_method_request(
            "invite",
            ("folder_name", "petname", "read-write"),
            b"POST",
            "http://invalid./experimental/magic-folder/folder_name/invite",
            b'{"participant-name": "petname", "mode": "read-write"}',
            {
                b"Content-Length": [b"53"],
            },
        )

    def test_invite_wait(self):
        """
        The /invite-wait API works
        """
        return self._client_method_request(
            "invite_wait",
            ("folder_name", "an-id"),
            b"POST",
            "http://invalid./experimental/magic-folder/folder_name/invite-wait",
            b'{"id": "an-id"}',
            {
                b"Content-Length": [b"15"],
            },
        )

    def test_list_invites(self):
        """
        The /list-invites API works
        """
        return self._client_method_request(
            "list_invites",
            ("folder_name", ),
            b"GET",
            "http://invalid./experimental/magic-folder/folder_name/invites",
        )

    def test_recent_changes(self):
        """
        The .../magic-folder/../recent-changes API works"
        """
        return self._client_method_request(
            "recent-changes",
            ("folder_name", 25),
            b"GET",
            "http://invalid./v1/magic-folder/folder_name/recent-changes",
            expected_query_args={
                b"number": [b"25"],
            }
        )

    def test_join(self):
        """
        The /join API works
        """
        folder_dir = FilePath(self.mktemp()).asTextMode()
        body = json.dumps(
            {
                "invite-code": "2-suspicious-penguin",
                "local-directory": folder_dir.path,
                "author": "amy",
                "poll-interval": 123,
                "scan-interval": 321,
            }
        ).encode("utf-8")

        return self._client_method_request(
            "join",
            ("folder_name", "2-suspicious-penguin", folder_dir, "amy", 123, 321),
            b"POST",
            "http://invalid./experimental/magic-folder/folder_name/join",
            body,
            {
                b"Content-Length": ["{}".format(len(body)).encode("utf8")],
            },
        )
