# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.tahoe_client``.
"""

from __future__ import (
    unicode_literals,
)

from json import (
    loads,
)

from io import (
    BytesIO,
)

from hyperlink import (
    DecodedURL,
)

from hypothesis import (
    given,
)

from hypothesis.strategies import (
    binary,
    text,
    dictionaries,
)

from testtools.matchers import (
    AfterPreprocessing,
    Equals,
)

from testtools.twistedsupport import (
    succeeded,
)

from ..tahoe_client import (
    create_tahoe_client
)

from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)

from .common import (
    SyncTestCase,
)

from .strategies import (
    filenodes,
)

ANY_ROOT = DecodedURL.from_text(u"http://example.invalid./")

class TahoeClientTests(SyncTestCase):
    """
    Tests for the client created by ``create_tahoe_client``.
    """
    def setup_example(self):
        """
        Create a fake Tahoe-LAFS web frontend and a client that will talk to it.
        """
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(ANY_ROOT, self.http_client)

    @given(binary())
    def test_create_immutable(self, data):
        """
        An immutable object can be stored with ``create_immutable``.
        """
        self.assertThat(
            self.tahoe_client.create_immutable(data),
            succeeded(
                # TODO Check that it's the kind of cap we expected?
                AfterPreprocessing(
                    lambda cap: self.root._uri.data[cap],
                    Equals(data),
                ),
            ),
        )

    @given(binary())
    def test_download_immutable(self, data):
        """
        An immutable object can be downloaded with ``download_capability``.
        """
        cap = self.root.add_data("URI:CHK:", data)
        self.assertThat(
            self.tahoe_client.download_capability(cap),
            succeeded(Equals(data)),
        )

    @given(binary())
    def test_stream_immutable(self, data):
        """
        An immutable object can be downloaded with ``stream_capability``.
        """
        cap = self.root.add_data("URI:CHK:", data)
        output = BytesIO()
        self.assertThat(
            self.tahoe_client.stream_capability(cap, output),
            succeeded(Equals(None)),
        )
        self.assertThat(
            output.getvalue(),
            Equals(data),
        )

    @given(
        # A shallow strategy for starters...
        dictionaries(
            text(),
            filenodes().map(lambda node: ["filenode", node]),
        ),
    )
    def test_create_immutable_directory(self, children):
        """
        An immutable directory can be created with ``create_immutable_directory``.
        """
        self.assertThat(
            self.tahoe_client.create_immutable_directory(children),
            succeeded(
                AfterPreprocessing(
                    lambda cap: loads(self.root._uri.data[cap]),
                    Equals(children),
                ),
            ),
        )
