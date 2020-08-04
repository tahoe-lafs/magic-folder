# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.tahoe_client``.
"""

from __future__ import (
    unicode_literals,
)

from functools import (
    partial,
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
    just,
)

from testtools.matchers import (
    MatchesPredicate,
    AfterPreprocessing,
    IsInstance,
    Equals,
    Always,
    Contains,
)

from testtools.twistedsupport import (
    succeeded,
    failed,
)

from twisted.internet.defer import (
    gatherResults,
)

from ..tahoe_client import (
    TahoeAPIError,
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
    tahoe_lafs_chk_capabilities,
    path_segments,
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
    def test_recreate_immutable(self, data):
        """
        If the data given to ``create_immutable`` already exists,
        ``create_immutable`` returns the cap of the existing data (which is
        the same as the cap of the new data).
        """
        def create_it():
            return self.tahoe_client.create_immutable(data)
        caps = []
        self.assertThat(
            create_it().addCallback(caps.append),
            succeeded(Always()),
        )
        self.assertThat(
            create_it(),
            succeeded(
                Equals(caps[0]),
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

    def _api_error_test(self, operation):
        """
        Assert that ``operation`` fails with ``TahoeAPIError``.
        """
        self.assertThat(
            operation(),
            failed(
                AfterPreprocessing(
                    lambda failure: failure.value,
                    IsInstance(TahoeAPIError),
                ),
            ),
        )

    @given(tahoe_lafs_chk_capabilities())
    def test_download_immutable_not_found(self, cap):
        """
        ``download_capability`` returns a ``Deferred`` that fails when a
        non-existant capability is requested.
        """
        self._api_error_test(
            partial(self.tahoe_client.download_capability, cap),
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

    @given(tahoe_lafs_chk_capabilities())
    def test_stream_immutable_not_found(self, cap):
        """
        ``stream_capability`` returns a ``Deferred`` that fails when a
        non-existant capability is requested.
        """
        output = BytesIO()
        self._api_error_test(
            partial(self.tahoe_client.stream_capability, cap, output),
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

    @given(
        just("foo"), #path_segments(),
        just(b"test"*200), #binary(),
    )
    def test_mutable_directory_add_child(self, child_name, content):
        """
        A child can be added to a mutable directory
        """
        mutable_d = self.tahoe_client.create_mutable_directory()
        child_d = self.tahoe_client.create_immutable(content)
        self.assertThat(gatherResults([mutable_d, child_d]), Always())

        mutable_cap = mutable_d.result
        child_cap = child_d.result

        self.tahoe_client.add_entry_to_mutable_directory(
            mutable_cap,
            child_name,
            child_cap,
        )

        child_uri = ANY_ROOT.child(u"uri", mutable_cap.decode("utf8"), child_name)
        resp_d = self.http_client.get(child_uri.to_text())
        self.assertThat(
            succeeded(resp_d),
            Always(),
        )
        child_content_d = resp_d.result.content()
        self.assertThat(
            succeeded(child_content_d),
            Always()
        )
        self.assertThat(child_content_d.result, Equals(content))
