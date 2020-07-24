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
    MatchesPredicate,
    AfterPreprocessing,
    Equals,
)

from testtools.twistedsupport import (
    succeeded,
)

from twisted.internet.defer import (
    gatherResults,
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

from .matchers import (
    contained_by,
)

ANY_ROOT = DecodedURL.from_text(u"http://example.invalid./")


def directory_children():
    # A shallow strategy for starters...
    return dictionaries(
        text(),
        filenodes().map(lambda node: ["filenode", node]),
    )

class TahoeClientTests(SyncTestCase):
    """
    Tests for the client created by ``create_tahoe_client``.
    """
    def setup_client(self):
        """
        Create a fake Tahoe-LAFS web frontend and a client that will talk to it.
        """
        self.root = create_fake_tahoe_root()
        self.http_client = create_tahoe_treq_client(self.root)
        self.tahoe_client = create_tahoe_client(ANY_ROOT, self.http_client)

    def setup_example(self):
        """
        Give every example tested by Hypothesis its own state.
        """
        self.setup_client()

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
        ignored, cap = self.root.add_data("URI:CHK:", data)
        self.assertThat(
            self.tahoe_client.download_capability(cap),
            succeeded(Equals(data)),
        )

    @given(binary())
    def test_stream_immutable(self, data):
        """
        An immutable object can be downloaded with ``stream_capability``.
        """
        ignored, cap = self.root.add_data("URI:CHK:", data)
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
        directory_children()
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

    def test_create_mutable_directory(self):
        """
        A mutable directory can be created with ``create_mutable_directory``.
        """
        self.setup_client()
        self.assertThat(
            self.tahoe_client.create_mutable_directory(),
            succeeded(
                contained_by(self.root._uri.data),
            ),
        )

    @given(directory_children())
    def test_mutable_directories_distinct(self, children):
        """
        Two mutable directories created with ``create_mutable_directory`` are
        distinct from one another.
        """
        a = self.tahoe_client.create_mutable_directory()
        b = self.tahoe_client.create_mutable_directory()
        self.assertThat(
            gatherResults([a, b]),
            succeeded(
                MatchesPredicate(
                    lambda caps: caps[0] != caps[1],
                    "Capabilities must be distinct: %r",
                ),
            ),
        )
