# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.participants``.
"""

from testtools.matchers import (
    IsInstance,
    AllMatch,
    MatchesAll,
)

from testtools.twistedsupport import (
    succeeded,
)

from .common import (
    SyncTestCase,
)

from ..participants import (
    participants_from_collective,
)


class CollectiveParticipantsTests(SyncTestCase):
    """
    Tests for a participants group backed by a Tahoe-LAFS directory (a
    "collective").
    """
    @given(
        lists(author_names()),
        readwrite_dirnodes(),
        readwrite_dirnodes(),
    )
    def test_list(self, author_names, upload_dircap, collective_dircap):
        """
        ``IParticipants.list`` returns a ``Deferred`` that fires with a list of
        ``IParticipant`` providers with names matching the names of the child
        directories in the collective.
        """
        client = ClientStandIn()
        upload_dirnode = client.create_node_from_uri(upload_dircap)
        collective_dirnode = client.create_node_from_uri(collective_dircap)

        self.assertThat(
            participants_from_collective(collective_dirnode, upload_dirnode),
            succeeded(
                MatchesAll(
                    IsInstance(list),
                    AllMatch(
                        Provides(IParticipant),
                    ),
                ),
            ),
        )
