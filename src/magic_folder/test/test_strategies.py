# Copyright 2020 The Magic-Folder Developers
# See COPYING for details.

"""
Tests for Hypothesis strategies for the test suite.
"""

from hypothesis import (
    given,
    assume,
)

from testtools.matchers import (
    Equals,
)

from twisted.python.filepath import (
    FilePath,
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
    def test_chk_roundtrips(self, cap):
        """
        Values built by ``tahoe_lafs_chk_capabilities`` round-trip through ASCII
        and ``allmydata.uri.from_string`` and their ``to_string`` method.
        """
        serialized = cap._tahoe_cap.to_string().decode("ascii")
        self.assertThat(
            cap.danger_real_capability_string(),
            Equals(serialized),
        )

    @given(tahoe_lafs_dir_capabilities())
    def test_dir_roundtrips(self, cap):
        """
        Values built by ``tahoe_lafs_dir_capabilities`` round-trip through ASCII
        and ``allmydata.uri.from_string`` and their ``to_string`` method.
        """
        serialized = cap._tahoe_cap.to_string().decode("ascii")
        self.assertThat(
            cap.danger_real_capability_string(),
            Equals(serialized),
        )

    @given(path_segments())
    def test_legal_path_segments(self, name):
        """
        Path segments build by ``path_segments`` are legal for use in the
        filesystem.
        """
        # Try to avoid accidentally scribbling all over the filesystem in the
        # test runner's environment if path_segments() ends up building
        # unfortunate values (/etc/passwd, /root/.bashrc, etc).
        assume(u"../" not in name)
        temp = FilePath(self.mktemp())
        temp.makedirs()

        # Now ask the platform if this path is alright or not.
        with temp.child(name).open("w"):
            pass
