# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Tests for Hypothesis strategies for the test suite.
"""

from os import (
    makedirs,
)

from hypothesis import (
    given,
    assume,
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
    path_segments,
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

    @given(path_segments())
    def test_legal(self, name):
        """
        Path segments build by ``path_segments`` are legal for use in the
        filesystem.
        """
        # Try to avoid accidentally scribbling all over the filesystem in the
        # test runner's environment if path_segments() ends up building
        # unfortunate values (/etc/passwd, /root/.bashrc, etc).
        assume(u"../" not in name)
        temp = self.mktemp()
        makedirs(temp)
        safe = (temp + b"/").decode("utf-8")

        # Now ask the platform if this path is alright or not.
        with open(safe + name, "w"):
            pass
