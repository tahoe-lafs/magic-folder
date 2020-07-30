# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.participants``.
"""

import attr

from json import (
    dumps,
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
    IsInstance,
    AllMatch,
    MatchesAll,
    AfterPreprocessing,
)

from testtools.twistedsupport import (
    succeeded,
)

from hypothesis import (
    given,
    assume,
)
from hypothesis.strategies import (
    booleans,
    integers,
    lists,
    tuples,
    dictionaries,
    sampled_from,
    one_of,
    just,
)

from allmydata.uri import (
    CHKFileURI,
    DirectoryURI,
    WriteableSSKFileURI,
    from_string as uri_from_string,
)
from allmydata.unknown import (
    UnknownNode,
)

from .common import (
    SyncTestCase,
)

from .strategies import (
    author_names,
    relative_paths,
    tahoe_lafs_dir_capabilities,
    tahoe_lafs_chk_capabilities,
)

from .matchers import (
    provides,
)

from ..testing.web import (
    create_fake_tahoe_root,
    create_tahoe_treq_client,
)

from ..magicpath import (
    path2magic,
)

from ..cli import (
    TahoeClient,
    Node,
)
from ..participants import (
    IParticipant,
    participant_from_dmd,
    participants_from_collective,
)

from ..snapshot import (
    format_filenode,
)

class CollectiveParticipantsTests(SyncTestCase):
    """
    Tests for a participants group backed by a Tahoe-LAFS directory (a
    "collective").
    """
    @given(
        sampled_from([
            object(),
            UnknownNode(b"", b""),
            Node(None, CHKFileURI(b"", b"", 0, 0, 0)),
        ]),
    )
    def test_invalid_upload_dirnode(self, upload_dirnode):
        """
        ``participants_from_collective`` raises ``TypeError`` if given an upload
        dirnode object which:
        * is not an ``IDirectoryNode`` provider,
        * has an unknown URI type
        * is not read-write
        """
        http_client = create_tahoe_treq_client()
        tahoe_client = TahoeClient(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )
        collective_dirnode = Node(
            tahoe_client,
            CHKFileURI(b"", b"", 0, 0, 0),
        )
        with ExpectedException(TypeError, "Upload dirnode was.*"):
            participants_from_collective(
                collective_dirnode,
                upload_dirnode,
            )

    @given(
        sampled_from([
            object(),
            UnknownNode(b"", b""),
            Node(None, WriteableSSKFileURI(b"", b"")),
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
        http_client = create_tahoe_treq_client()
        tahoe_client = TahoeClient(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )
        upload_dirnode = Node(
            None,
            DirectoryURI(),
        )
        with ExpectedException(TypeError, "Collective dirnode was.*"):
            participants_from_collective(
                collective_dirnode,
                upload_dirnode,
            )

    @given(
        dictionaries(
            author_names(),
            tahoe_lafs_dir_capabilities(),
        ),
        tahoe_lafs_dir_capabilities(),
        tahoe_lafs_dir_capabilities(),
    )
    def test_list(self, collective_contents, rw_collective_dircap, upload_dircap):
        """
        ``IParticipants.list`` returns a ``Deferred`` that fires with a list of
        ``IParticipant`` providers with names matching the names of the child
        directories in the collective.
        """
        assume(rw_collective_dircap != upload_dircap)
        rw_collective_diruri = uri_from_string(rw_collective_dircap.encode("ascii"))
        upload_diruri = uri_from_string(upload_dircap.encode("ascii"))

        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = TahoeClient(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )

        collective_dirnode = Node(
            tahoe_client,
            rw_collective_diruri.get_readonly(),
        )
        root._uri.data[collective_dirnode.get_uri()] = dumps([
            u"dirnode",
            {u"children": {
                name: format_filenode(cap, {})
                for (name, cap)
                in collective_contents.items()
            }},
        ])

        upload_dirnode = Node(
            tahoe_client,
            upload_diruri,
        )
        root._uri.data[upload_dirnode.get_uri()] = dumps([
            u"dirnode",
            {u"children": {}},
        ])

        participants = participants_from_collective(
            collective_dirnode,
            upload_dirnode,
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
                        # There should be one self if our upload dircap found
                        # its way into the collective, zero otherwise.
                        lambda ps: len({p for p in ps if p.is_self}),
                        Equals(int(upload_dircap in collective_contents.values())),
                    )
                ),
            ),
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
        ``IParticipant.list`` returns a ``Deferred`` that fires with a ``dict``
        mapping relative file paths to ``IFolderFile`` providers.
        """
        root = create_fake_tahoe_root()
        http_client = create_tahoe_treq_client(root)
        tahoe_client = TahoeClient(
            DecodedURL.from_text(u"http://example.invalid./"),
            http_client,
        )

        upload_diruri = uri_from_string(upload_dircap.encode("ascii"))
        upload_dirnode = Node(
            tahoe_client,
            upload_diruri,
        )
        root._uri.data[upload_dirnode.get_uri()] = dumps([
            u"dirnode",
            {u"children": {
                path2magic(name): format_filenode(cap, {u"version": version})
                for (name, (cap, version))
                in children.items()
            }},
        ])

        participant = participant_from_dmd(
            author_name,
            upload_dirnode,
            is_self,
        )

        self.assertThat(
            participant.list(),
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
