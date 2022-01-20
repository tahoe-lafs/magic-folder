# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.tahoe_client``.
"""

from functools import (
    partial,
)

from json import (
    loads,
    dumps,
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
    ContainsDict,
    MatchesPredicate,
    AfterPreprocessing,
    IsInstance,
    Equals,
    Always,
)

from testtools.twistedsupport import (
    succeeded,
    failed,
)

from twisted.internet.defer import (
    gatherResults,
)
from twisted.web.http import (
    GONE,
)

from ..tahoe_client import (
    TahoeAPIError,
    create_tahoe_client
)
from ..util.capabilities import (
    Capability,
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
    tahoe_lafs_dir_capabilities,
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
                    lambda cap: self.root._uri.data[cap.danger_real_capability_string()],
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
            self.tahoe_client.download_file(Capability.from_string(cap)),
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
            partial(self.tahoe_client.download_file, cap),
        )

    @given(tahoe_lafs_dir_capabilities())
    def test_download_immutable_wrong_kind(self, cap):
        """
        ``download_capability`` returns a ``Deferred`` that fails when a
        directory-capability is downloaded
        """
        self.assertThat(
            self.tahoe_client.download_file(cap),
            failed(
                AfterPreprocessing(
                    lambda failure: str(failure.value),
                    Equals("{} is not a file capability".format(cap))
                )
            )
        )

    @given(tahoe_lafs_chk_capabilities())
    def test_list_directory_non_exist(self, cap):
        """
        ``list_directory`` returns a ``Deferred`` that fails when a
        non-existing capability is requested.
        """
        self._api_error_test(
            partial(self.tahoe_client.list_directory, cap),
        )

    @given(tahoe_lafs_dir_capabilities())
    def test_directory_data_non_exist(self, cap):
        """
        ``directory_data`` returns a ``Deferred`` that fails when a
        non-existing capability is requested.
        """
        self._api_error_test(
            partial(self.tahoe_client.directory_data, cap),
        )

    def test_list_directory_wrong_kind(self):
        """
        ``list_directory`` returns a ``Deferred`` that fails when given a
        non-directory capability
        """
        self.setup_example()
        data = dumps([
            "filenode",
            {
                "mutable": False,
                "verify_uri": "URI:CHK-Verifier:vi5xqgkyo6ns46ksq44mzqy42u:lrimqiz4fyvqhfruf25rt56ncdsqojlu66hih3lkeen4lh3vgvjq:1:5:6798975",
                "format": "CHK",
                "ro_uri": "URI:CHK:lfnzol6woyz42falzttgxrvth4:lrimqiz4fyvqhfruf25rt56ncdsqojlu66hih3lkeen4lh3vgvjq:1:5:6798975",
                "size": 6798975
            }
        ]).encode("utf8")
        _, cap = self.root.add_data("URI:CHK:", data)
        self.assertThat(
            self.tahoe_client.list_directory(Capability.from_string(cap)),
            failed(
                AfterPreprocessing(
                    lambda fail: str(fail.value),
                    Equals("Capability is a 'filenode' not a 'dirnode'")
                )
            )
        )

    def test_directory_data_wrong_kind(self):
        """
        ``directory_data`` returns a ``Deferred`` that fails when given a
        non-directory capability
        """
        self.setup_example()
        data = dumps([
            "filenode",
            {
                "mutable": False,
                "verify_uri": "URI:CHK-Verifier:vi5xqgkyo6ns46ksq44mzqy42u:lrimqiz4fyvqhfruf25rt56ncdsqojlu66hih3lkeen4lh3vgvjq:1:5:6798975",
                "format": "CHK",
                "ro_uri": "URI:CHK:lfnzol6woyz42falzttgxrvth4:lrimqiz4fyvqhfruf25rt56ncdsqojlu66hih3lkeen4lh3vgvjq:1:5:6798975",
                "size": 6798975
            }
        ]).encode("utf8")
        _, cap = self.root.add_data("URI:CHK:", data)
        self.assertThat(
            self.tahoe_client.list_directory(Capability.from_string(cap)),
            failed(
                AfterPreprocessing(
                    lambda fail: str(fail.value),
                    Equals("Capability is a 'filenode' not a 'dirnode'")
                )
            )
        )

    def test_directory_data_wrong_cap_type(self):
        """
        ``directory_data`` returns a ``Deferred`` that fails when given a
        non-directory capability
        """
        self.setup_example()
        data = dumps([
            "filenode",
            {
                "mutable": False,
                "verify_uri": "URI:CHK-Verifier:vi5xqgkyo6ns46ksq44mzqy42u:lrimqiz4fyvqhfruf25rt56ncdsqojlu66hih3lkeen4lh3vgvjq:1:5:6798975",
                "format": "CHK",
                "ro_uri": "URI:CHK:lfnzol6woyz42falzttgxrvth4:lrimqiz4fyvqhfruf25rt56ncdsqojlu66hih3lkeen4lh3vgvjq:1:5:6798975",
                "size": 6798975
            }
        ]).encode("utf8")
        _, cap = self.root.add_data("URI:CHK:", data)
        self.assertThat(
            self.tahoe_client.directory_data(Capability.from_string(cap)),
            failed(
                AfterPreprocessing(
                    lambda fail: str(fail.value),
                    Equals("[REDACTED] is not a directory-capability")
                )
            )
        )

    @given(binary())
    def test_stream_immutable(self, data):
        """
        An immutable object can be downloaded with ``stream_capability``.
        """
        ignored, cap = self.root.add_data("URI:CHK:", data)
        output = BytesIO()
        self.assertThat(
            self.tahoe_client.stream_capability(Capability.from_string(cap), output),
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
                    lambda cap: loads(self.root._uri.data[cap.danger_real_capability_string()])[1]["children"],
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
                AfterPreprocessing(
                    lambda cap: cap.danger_real_capability_string(),
                    contained_by(self.root._uri.data),
                ),
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
        self.assertThat(gatherResults([mutable_d, child_d]), succeeded(Always()))

        mutable_cap = mutable_d.result
        child_cap = child_d.result

        self.tahoe_client.add_entry_to_mutable_directory(
            mutable_cap,
            child_name,
            child_cap,
        )

        child_uri = ANY_ROOT.child(u"uri", mutable_cap.danger_real_capability_string(), child_name)
        resp_d = self.http_client.get(child_uri.to_text())
        self.assertThat(
            resp_d,
            succeeded(Always()),
        )
        child_content_d = resp_d.result.content()
        self.assertThat(
            child_content_d,
            succeeded(Always()),
        )
        self.assertThat(child_content_d.result, Equals(content))

        # getting a different child fails
        child_uri = ANY_ROOT.child(u"uri", mutable_cap.danger_real_capability_string(), u"not-the-child-name")
        resp_d = self.http_client.get(child_uri.to_text())
        self.assertThat(
            resp_d,
            succeeded(Always()),
        )
        self.assertThat(resp_d.result.code, Equals(GONE))

    def test_get_welcome(self):
        """
        We can retrieve the welcome page
        """
        self.setup_example()
        self.assertThat(
            self.tahoe_client.get_welcome(),
            succeeded(
                ContainsDict({
                    "introducers": Always(),
                    "servers": Always(),
                })
            )
        )
