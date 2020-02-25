# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Tests for Hypothesis strategies for the test suite.
"""

from hypothesis import (
    given,
)

from allmydata.uri import (
    from_string as cap_from_string,
)

from testtools.matchers import (
    Equals,
)

from .common import (
    SyncTestCase,
)

from .strategies import (
    tahoe_lafs_chk_capabilities,
    tahoe_lafs_dir_capabilities,
)

class StrategyTests(SyncTestCase):
    """
    Tests for various strategies.
    """
    @given(tahoe_lafs_chk_capabilities())
    def test_chk_roundtrips(self, cap_text):
        """
        Values built by ``tahoe_lafs_chk_capabilities`` round-trip through ASCII
        and ``allmydata.uri.from_string`` and their ``to_string`` method.
        """
        cap = cap_from_string(cap_text.encode("ascii"))
        serialized = cap.to_string().decode("ascii")
        self.assertThat(
            cap_text,
            Equals(serialized),
        )

    @given(tahoe_lafs_dir_capabilities())
    def test_dir_roundtrips(self, cap_text):
        """
        Values built by ``tahoe_lafs_dir_capabilities`` round-trip through ASCII
        and ``allmydata.uri.from_string`` and their ``to_string`` method.
        """
        cap = cap_from_string(cap_text.encode("ascii"))
        serialized = cap.to_string().decode("ascii")
        self.assertThat(
            cap_text,
            Equals(serialized),
        )
