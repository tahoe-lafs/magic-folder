# -*- coding: utf-8 -*-
# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2020 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

"""
Tests for the allmydata.testing helpers

NOTE: this is code copied from Tahoe-LAFS until there is a release
after 1.14.0 that we can depend on. Once 1.15.0 or later is release,
this code can be deleted.
"""

from twisted.web.http import (
    GONE,
)

from eliot.twisted import (
    inline_callbacks,
)

from allmydata.uri import (
    from_string,
    CHKFileURI,
)
from magic_folder.testing.web import (
    create_tahoe_treq_client,
    capability_generator,
)
from magic_folder.tahoe_client import (
    create_tahoe_client,
)

from hyperlink import (
    DecodedURL,
)

from hypothesis import (
    given,
)
from hypothesis.strategies import (
    binary,
)

from testtools import (
    TestCase,
)
from testtools.matchers import (
    Always,
    Equals,
    IsInstance,
    MatchesStructure,
    AfterPreprocessing,
)
from testtools.twistedsupport import (
    succeeded,
)


class FakeWebTest(TestCase):
    """
    Test the WebUI verified-fakes infrastucture
    """

    # Note: do NOT use setUp() because Hypothesis doesn't work
    # properly with it. You must instead do all fixture-type work
    # yourself in each test.

    @given(
        content=binary(),
    )
    def test_create_and_download(self, content):
        """
        Upload some content (via 'PUT /uri') and then download it (via
        'GET /uri?uri=...')
        """
        http_client = create_tahoe_treq_client()

        @inline_callbacks
        def do_test():
            resp = yield http_client.put("http://example.com/uri", content)
            self.assertThat(resp.code, Equals(201))

            cap_raw = yield resp.content()
            cap = from_string(cap_raw)
            self.assertThat(cap, IsInstance(CHKFileURI))

            resp = yield http_client.get(
                "http://example.com/uri?uri={}".format(cap.to_string().decode("ascii"))
            )
            self.assertThat(resp.code, Equals(200))

            round_trip_content = yield resp.content()

            # using the form "/uri/<cap>" is also valid

            resp = yield http_client.get(
                "http://example.com/uri/{}".format(cap.to_string().decode("ascii"))
            )
            self.assertEqual(resp.code, 200)

            round_trip_content = yield resp.content()
            self.assertEqual(content, round_trip_content)
        self.assertThat(
            do_test(),
            succeeded(Always()),
        )

    @given(
        content=binary(),
    )
    def test_duplicate_upload(self, content):
        """
        Upload the same content (via 'PUT /uri') twice
        """

        http_client = create_tahoe_treq_client()

        @inline_callbacks
        def do_test():
            resp = yield http_client.put("http://example.com/uri", content)
            self.assertEqual(resp.code, 201)

            cap_raw = yield resp.content()
            self.assertThat(
                cap_raw,
                AfterPreprocessing(
                    from_string,
                    IsInstance(CHKFileURI)
                )
            )

            resp = yield http_client.put("http://example.com/uri", content)
            self.assertThat(resp.code, Equals(200))
        self.assertThat(
            do_test(),
            succeeded(Always()),
        )

    def test_download_missing(self):
        """
        If a capability is requested for which the stored cyphertext cannot be
        located, **GET /uri?uri=CAP** returns a GONE response code.
        """

        http_client = create_tahoe_treq_client()
        cap_gen = capability_generator("URI:CHK:")

        uri = DecodedURL.from_text(u"http://example.com/uri?uri={}".format(next(cap_gen)))
        resp = http_client.get(uri.to_uri().to_text())

        self.assertThat(
            resp,
            succeeded(
                MatchesStructure(
                    code=Equals(GONE),
                )
            )
        )

    def test_download_no_arg(self):
        """
        Error if we GET from "/uri" with no ?uri= query-arg
        """

        http_client = create_tahoe_treq_client()

        uri = DecodedURL.from_text(u"http://example.com/uri/")
        resp = http_client.get(uri.to_uri().to_text())

        self.assertThat(
            resp,
            succeeded(
                MatchesStructure(
                    code=Equals(400)
                )
            )
        )

    @inline_callbacks
    def test_add_directory_entry(self):
        """
        Adding a capability to a mutable directory
        """
        content = b"content " * 200
        http_client = create_tahoe_treq_client()
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.com/"),
            http_client,
        )

        # first create a mutable directory
        mut_cap = yield tahoe_client.create_mutable_directory()

        # create an immutable and link it into the directory
        file_cap = yield tahoe_client.create_immutable(content)
        yield tahoe_client.add_entry_to_mutable_directory(mut_cap, u"foo", file_cap)

        # prove we can access the expected file via a GET
        uri = DecodedURL.from_text(u"http://example.com/uri/")
        uri = uri.child(mut_cap.danger_real_capability_string(), u"foo")
        resp = http_client.get(uri.to_uri().to_text())

        self.assertThat(
            resp,
            succeeded(
                MatchesStructure(
                    code=Equals(200),
                )
            )
        )
        self.assertThat(
            resp.result.content(),
            succeeded(Equals(content)),
        )
