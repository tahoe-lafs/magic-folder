# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.client``.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

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
            lambda: "fake token",
        )

    def setUp(self):
        super(MagicFolderClientTests, self).setUp()
        self.setup_client()

    def test_tahoe_objects(self):
        """
        The /tahoe-objects API works
        """
        self.assertThat(
            self.client.tahoe_objects("a_magic_folder"),
            succeeded(Always()),
        )
        self.assertThat(
            self.api_calls,
            Equals([
                ('GET',
                 'http://invalid./v1/magic-folder/a_magic_folder/tahoe-objects',
                 {},
                 {
                     'Accept-Encoding': ['gzip'],
                     'Authorization': ['Bearer fake token'],
                     'Connection': ['close'],
                     'Host': ['invalid.'],
                 },
                 '',
                ),
            ])
        )
