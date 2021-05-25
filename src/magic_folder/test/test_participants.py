# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.participants``.
"""

import os
from json import (
    dumps,
)

from nacl.signing import (
    VerifyKey,
)

from hyperlink import (
    DecodedURL,
)

from testtools import (
    ExpectedException,
)
from testtools.matchers import (
    Always,
    Equals,
    StartsWith,
    IsInstance,
    AllMatch,
    MatchesAll,
    AfterPreprocessing,
)

from testtools.twistedsupport import (
    succeeded,
    failed,
)

from hypothesis import (
    given,
    assume,
)
from hypothesis.strategies import (
    booleans,
    integers,
    tuples,
    dictionaries,
    sampled_from,
    one_of,
    just,
    lists,
)

from .common import (
    SyncTestCase,
)

from .strategies import (
    author_names,
    relative_paths,
    tahoe_lafs_dir_capabilities,
    tahoe_lafs_readonly_dir_capabilities,
    tahoe_lafs_chk_capabilities,
    unique_value_dictionaries,
)

from .matchers import (
    provides,
)

from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)
from ..util.capabilities import (
    to_readonly_capability,
)
from ..magicpath import (
    path2magic,
)
from ..tahoe_client import (
    TahoeClient,
    create_tahoe_client,
)
from ..participants import (
    IParticipant,
    participant_from_dmd,
    participants_from_collective,
)
from ..snapshot import (
    RemoteAuthor,
    create_local_author,
)

from ..snapshot import (
    format_filenode,
)

from ..util.encoding import (
    normalize
)

class CollectiveParticipantsTests(SyncTestCase):
    """
    Tests for a participants group backed by a Tahoe-LAFS directory (a
    "collective").
    """
    @given(
        sampled_from([
            b"",
            b"URI:CHK:::0:0:0",
        ]),
    )
    def test_invalid_upload_dirnode(self, upload_dircap):
        """
        ``participants_from_collective`` raises ``TypeError`` if given an upload
        dirnode object which:
        * is not an ``IDirectoryNode`` provider,
        * has an unknown URI type
        * is not read-write
        """
        tahoe_client = None
        collective_dircap = b"URI:DIR2:txkwxzcha3nfqtcv45a7wzri5i:2tlulhdv24a6t6jy73rvlhxncsj7nqf46zzh3d6zjvb7lkzolx7a"
        with ExpectedException(TypeError, "Upload dirnode was.*"):
            participants_from_collective(
                collective_dircap,
                upload_dircap,
                tahoe_client,
            )

    @given(
        sampled_from([
            b"",
            b"URI:SSK::",
        ]),
    )
    def test_invalid_collective_dirnode(self, collective_dirnode):
        """
        ``participants_from_collective`` raises ``TypeError`` if given an upload
        dirnode object which:
        * is not an ``IDirectoryNode`` provider,
        * has an unknown URI type
        * is not read-only
        """
        tahoe_client = None
        upload_dircap = b"URI:DIR2:txkwxzcha3nfqtcv45a7wzri5i:2tlulhdv24a6t6jy73rvlhxncsj7nqf46zzh3d6zjvb7lkzolx7a"
        with ExpectedException(TypeError, "Collective dirnode was.*"):
            participants_from_collective(
                collective_dirnode,
                upload_dircap,
                tahoe_client,
            )

    @given(
        unique_value_dictionaries(
            author_names(),
            tahoe_lafs_dir_capabilities(),
            min_size=1,
        ),
        tahoe_lafs_dir_capabilities(),
    )
    def test_list(self, collective_contents, rw_collective_dircap):
        """
        ``IParticipants.list`` returns a ``Deferred`` that fires with a list of
        ``IParticipant`` providers with names matching the names of the child
        directories in the collective.
        """
        # The collective can't be anyone's DMD.
        assume(rw_collective_dircap not in collective_contents.values())

        # Pick someone in the collective to be us.
        author = sorted(collective_contents)[0]
        upload_dircap = collective_contents[author]

        rw_collective_dircap = rw_collective_dircap.encode("ascii")
        upload_dircap = upload_dircap.encode("ascii")

        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = TahoeClient(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )

        root._uri.data[rw_collective_dircap] = dumps([
            u"dirnode",
            {u"children": {
                name: format_filenode(cap, {})
                for (name, cap)
                in collective_contents.items()
            }},
        ])

        root._uri.data[upload_dircap] = dumps([
            u"dirnode",
            {u"children": {}},
        ])

        participants = participants_from_collective(
            rw_collective_dircap,
            upload_dircap,
            tahoe_client,
        )

        self.assertThat(
            participants.list(),
            succeeded(
                MatchesAll(
                    IsInstance(list),
                    AllMatch(
                        provides(IParticipant),
                    ),
                    AfterPreprocessing(
                        lambda ps: sorted(p.name for p in ps),
                        Equals(sorted(collective_contents)),
                    ),
                    AfterPreprocessing(
                        # There should be exactly one participant that signals
                        # it is us.  We know it will be there because we
                        # selected our dircap from among all those DMDs in the
                        # collective at the top.
                        lambda ps: len({p for p in ps if p.is_self}),
                        Equals(1),
                    )
                ),
            ),
        )

    @given(
        unique_value_dictionaries(
            author_names(),
            tahoe_lafs_dir_capabilities(),
            min_size=2,
        ),
        tahoe_lafs_dir_capabilities(),
    )
    def test_add(self, collective_contents, rw_collective_dircap):
        """
        ``IParticipants.add`` correctly adds a new, previously unknown
        participant.
        """
        # The collective can't be anyone's DMD.
        assume(rw_collective_dircap not in collective_contents.values())
        rw_collective_dircap = rw_collective_dircap.encode("ascii")

        # Pick someone in the collective to be us.
        author = sorted(collective_contents)[0]
        upload_dircap = collective_contents[author].encode("ascii")
        upload_dircap_ro = to_readonly_capability(upload_dircap)

        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = TahoeClient(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )

        root._uri.data[rw_collective_dircap] = dumps([
            u"dirnode",
            {
                u"children": {
                    normalize(author): format_filenode(upload_dircap_ro, {}),
                },
            },
        ])

        root._uri.data[upload_dircap] = dumps([
            u"dirnode",
            {u"children": {}},
        ])

        participants = participants_from_collective(
            rw_collective_dircap,
            upload_dircap,
            tahoe_client,
        )

        # add all the "other" participants using .add() API
        for name, dircap in collective_contents.items():
            if name == author:
                continue
            participants.add(
                RemoteAuthor(name, VerifyKey(os.urandom(32))),
                to_readonly_capability(dircap),
            )

        # confirm we added all the right participants by using the
        # list() API
        self.assertThat(
            participants.list(),
            succeeded(
                MatchesAll(
                    IsInstance(list),
                    AllMatch(
                        provides(IParticipant),
                    ),
                    AfterPreprocessing(
                        lambda ps: sorted(p.name for p in ps),
                        Equals(sorted(map(normalize, collective_contents))),
                    ),
                    AfterPreprocessing(
                        lambda ps: sorted(p.dircap for p in ps),
                        Equals(sorted(
                            to_readonly_capability(c)
                            for c in collective_contents.values()
                        )),
                    ),
                    AfterPreprocessing(
                        # There should be exactly one participant that signals
                        # it is us.  We know it will be there because we
                        # selected our dircap from among all those DMDs in the
                        # collective at the top.
                        lambda ps: len({p for p in ps if p.is_self}),
                        Equals(1),
                    )
                ),
            ),
        )

    @given(
        author_names(),
        tahoe_lafs_dir_capabilities(),
        tahoe_lafs_dir_capabilities(),
        tahoe_lafs_dir_capabilities(),
    )
    def test_add_writable_dmd(self, author, rw_collective_dircap, rw_upload_dircap, personal_dmd):
        """
        Calling ``IParticipants.add`` with a read-write Personal DMD
        reports an error.
        """
        assume(rw_collective_dircap != rw_upload_dircap)
        assume(rw_collective_dircap != personal_dmd)
        assume(rw_upload_dircap != personal_dmd)
        # we are testing error-cases, so don't need a real client
        participants = participants_from_collective(
            rw_collective_dircap,
            rw_upload_dircap,
            tahoe_client=None,
        )

        self.assertThat(
            participants.add(
                create_local_author(author).to_remote_author(),
                personal_dmd,
            ),
            failed(
                AfterPreprocessing(
                    lambda f: str(f.value),
                    Equals(
                        "New participant Personal DMD must be read-only dircap"
                    )
                )
            )
        )

    @given(
        author_names(),
        tahoe_lafs_dir_capabilities(),
        tahoe_lafs_dir_capabilities(),
        tahoe_lafs_readonly_dir_capabilities(),
    )
    def test_add_wrong_author_type(self, author, rw_collective_dircap, rw_upload_dircap, personal_dmd):
        """
        ``IParticipants.add`` called with non-RemoteAuthor instance fails.
        """
        assume(rw_collective_dircap != rw_upload_dircap)
        # we are testing error-cases, so don't need a real client
        participants = participants_from_collective(
            rw_collective_dircap,
            rw_upload_dircap,
            tahoe_client=None,
        )

        self.assertThat(
            participants.add(
                create_local_author(author),
                personal_dmd,
            ),
            failed(
                AfterPreprocessing(
                    lambda f: unicode(f.value),
                    Equals(
                        "Author must be a RemoteAuthor instance"
                    )
                )
            )
        )

    @given(
        lists(
            author_names(),
            min_size=2,
            max_size=2,
        ),
        tahoe_lafs_dir_capabilities(),
        tahoe_lafs_dir_capabilities(),
        lists(tahoe_lafs_readonly_dir_capabilities(), min_size=2, max_size=2, unique=True),
    )
    def test_add_duplicate(self, authors, rw_collective_dircap, rw_upload_dircap, personal_dmds):
        """
        Adding two participants with the same Personal DMD or same name
        produces an error
        """
        # authors must be different
        assume(normalize(authors[0]) != normalize(authors[1]))
        # none of the capabilities should be the same
        all_caps = [rw_collective_dircap, rw_upload_dircap] + personal_dmds
        for a in range(len(all_caps)):
            for b in range(len(all_caps)):
                if a != b:
                    assume(all_caps[a] != all_caps[b])

        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = create_tahoe_client(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )
        root._uri.data[rw_collective_dircap] = dumps([
            u"dirnode",
            {
                u"children": {},
            }
        ])

        participants = participants_from_collective(
            rw_collective_dircap,
            rw_upload_dircap,
            tahoe_client=tahoe_client,
        )

        # add our first participant; should be uncontroversial
        self.assertThat(
            participants.add(
                create_local_author(authors[0]).to_remote_author(),
                personal_dmds[0],
            ),
            succeeded(Always()),
        )

        # second participant (with same Personal DMD) should be an
        # error
        self.assertThat(
            participants.add(
                create_local_author(authors[1]).to_remote_author(),
                personal_dmds[0],
            ),
            failed(
                AfterPreprocessing(
                    lambda f: unicode(f.value),
                    StartsWith(
                        "Already have a participant with Personal DMD"
                    )
                )
            )
        )

        # second participant with *different* Personal DMD but the
        # same name should be an error
        self.assertThat(
            participants.add(
                create_local_author(authors[0]).to_remote_author(),
                personal_dmds[1],
            ),
            failed(
                AfterPreprocessing(
                    lambda f: unicode(f.value),
                    StartsWith(
                        "Already have a participant called"
                    )
                )
            )
        )


class CollectiveParticipantTests(SyncTestCase):
    """
    Tests for a participant backed by a Magic-Folder "DMD"-style Tahoe-LAFS
    directory.
    """
    @given(
        author_names(),
        tahoe_lafs_dir_capabilities(),
        booleans(),
        dictionaries(
            one_of(
                # XXX
                # The relative_paths space is huge. :/ We should probably make
                # it smaller.  Over many test runs it never generated a path
                # with @@ or @_ in it.  So make those cases a bit more likely.
                just(u"@@"),
                just(u"@_"),
                relative_paths(),
            ),
            tuples(tahoe_lafs_chk_capabilities(), integers()),
        )
    )
    def test_list(self, author_name, upload_dircap, is_self, children):
        """
        ``IParticipant.files`` returns a ``Deferred`` that fires with a ``dict``
        mapping relative file paths to ``FolderFile`` providers.
        """
        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = TahoeClient(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )

        upload_dircap = upload_dircap.encode("ascii")
        root._uri.data[upload_dircap] = dumps([
            u"dirnode",
            {u"children": {
                path2magic(name): format_filenode(cap, {u"version": version})
                for (name, (cap, version))
                in children.items()
            }},
        ])

        participant = participant_from_dmd(
            author_name,
            upload_dircap,
            is_self,
            tahoe_client,
        )

        self.assertThat(
            participant.files(),
            succeeded(
                AfterPreprocessing(
                    lambda result: {
                        name: f.version
                        for (name, f)
                        in result.items()
                    },
                    Equals({
                        name: version
                        for (name, (cap, version))
                        in children.items()
                    }),
                ),
            ),
        )
