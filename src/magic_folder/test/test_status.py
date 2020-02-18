# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Tests for ``magic_folder.status``.
"""

from __future__ import (
    unicode_literals,
)

from json import (
    dumps,
)

import attr

from hypothesis import (
    given,
    assume,
)

from hypothesis.strategies import (
    lists,
)

from testtools.matchers import (
    AfterPreprocessing,
    Equals,
    raises,
)
from testtools.twistedsupport import (
    failed,
    succeeded,
)

from twisted.python.failure import (
    Failure,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.web.resource import (
    Resource,
)
from twisted.web.static import (
    Data,
)

from treq.testing import (
    RequestTraversalAgent,
    StubTreq,
)
from treq.client import (
    HTTPClient,
)

from allmydata.uri import (
    from_string as cap_from_string,
)

from .common import (
    AsyncTestCase,
)

from .strategies import (
    folder_names,
    absolute_paths,
    tahoe_lafs_dir_capabilities as dircaps,
    tokens,
)

from .fixtures import (
    NodeDirectory,
)

from .agentutil import (
    FailingAgent,
)

from ..web.magic_folder import (
    MagicFolderWebApi,
)

from ..status import (
    BadFolderName,
    Status,
    status,
)

class StatusTests(AsyncTestCase):
    """
    Tests for ``magic_folder.status.status``.
    """
    @given(folder_names(), absolute_paths().map(FilePath))
    def test_missing_node(self, folder_name, node_directory):
        """
        If the given node directory does not exist, ``status`` raises
        ``EnvironmentError``.
        """
        assume(not node_directory.exists())
        treq = object()
        self.assertThat(
            lambda: status(folder_name, node_directory, treq),
            raises(EnvironmentError),
        )

    @given(folder_names())
    def test_missing_api_auth_token(self, folder_name):
        """
        If the given node directory does not contain an API authentication token,
        ``status`` raises ``EnvironmentError``.
        """
        node_directory = FilePath(self.mktemp())
        node_directory.makedirs()
        treq = object()
        self.assertThat(
            lambda: status(folder_name, node_directory, treq),
            raises(EnvironmentError),
        )

    @given(lists(
        folder_names(),
        unique=True,
        min_size=1,
        # Just keep the test from taking forever to run ...
        max_size=10,
    ), dircaps(), dircaps())
    def test_unknown_magic_folder_name(self, folder_names, collective_dircap, upload_dircap):
        """
        If a name which does not correspond to an existing magic folder is given,
        ``status`` raises ``BadFolderName``.
        """
        assume(collective_dircap != upload_dircap)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory))

        treq = object()
        nonexistent_folder_name = folder_names.pop()

        for folder_name in folder_names:
            node.create_magic_folder(
                folder_name,
                collective_dircap,
                upload_dircap,
                tempdir.child(u"folder"),
                60,
            )

        self.assertThat(
            lambda: status(nonexistent_folder_name, node_directory, treq),
            raises(BadFolderName),
        )

    @given(folder_names(), dircaps(), dircaps())
    def test_failed_node_connection(self, folder_name, collective_dircap, upload_dircap):
        """
        If an HTTP request to the Tahoe-LAFS node fails, ``status`` returns a
        ``Deferred`` that fails with that failure.
        """
        assume(collective_dircap != upload_dircap)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory))

        node.create_magic_folder(
            folder_name,
            collective_dircap,
            upload_dircap,
            tempdir.child(u"folder"),
            60,
        )

        exception = Exception("Made up failure")
        treq = HTTPClient(FailingAgent(Failure(exception)))
        self.assertThat(
            status(folder_name, node_directory, treq),
            failed(
                AfterPreprocessing(
                    lambda f: f.value,
                    Equals(exception),
                ),
            ),
        )

    @given(folder_names(), dircaps(), dircaps(), tokens())
    def test_status(self, folder_name, collective_dircap, upload_dircap, token):
        """
        ``status`` returns a ``Deferred`` that fires with a ``Status`` instance
        reflecting the status of the identified magic folder.
        """
        assume(collective_dircap != upload_dircap)

        tempdir = FilePath(self.mktemp())
        node_directory = tempdir.child(u"node")
        node = self.useFixture(NodeDirectory(node_directory, token))
        local_folder = tempdir.child(u"folder")
        local_folder.makedirs()
        node.create_magic_folder(
            folder_name,
            collective_dircap,
            upload_dircap,
            local_folder,
            60,
        )

        local_files = {
            u"foo": {},
        }
        remote_files = {
            u"participant-name": {
                u"foo": node_json(u"foo"),
            },
        }
        folders = {
            folder_name: StubMagicFolder(),
        }
        treq = StubTreq(magic_folder_uri_hierarchy(
            folders,
            collective_dircap,
            upload_dircap,
            local_files,
            remote_files,
            token,
        ))
        self.assertThat(
            status(folder_name, node_directory, treq),
            succeeded(
                Equals(Status(
                    folder_name=folder_name,
                    local_files=local_files,
                    remote_files=remote_files,
                    folder_status=None,
                )),
            ),
        )

def magic_folder_uri_hierarchy(
        folders,
        collective_dircap,
        upload_dircap,
        local_files,
        remote_files,
        token,
):
    upload_json = dirnode_json(
        upload_dircap, {
            key: node_json(upload_dircap)
            for key
            in local_files
        },
    )
    upload = Data(
        dumps(upload_json),
        b"text/plain",
    )
    collective_json = dirnode_json(
        collective_dircap, {
            key: dirnode_json(upload_dircap, {})
            for key
            in remote_files
        },
    )
    collective = Data(
        dumps(collective_json),
        b"text/plain",
    )

    uri = Resource()
    uri.putChild(
        upload_dircap,
        upload,
    )
    uri.putChild(
        cap_from_string(upload_dircap.encode("ascii")).get_readonly().to_string(),
        upload,
    )
    uri.putChild(
        collective_dircap,
        collective,
    )

    api = MagicFolderWebApi(
        get_magic_folder=lambda name: folders[name],
        get_auth_token=lambda: token,
    )

    root = Resource()
    # Unfortunately the following two resource hierarchies should live at
    # different servers.  However, we lack multi-server support in our web
    # testing library.  So, they live side-by-side.  I hope that if the
    # implementation mistakenly sends requests to the wrong server it will be
    # blindingly obvious and this test shortcoming will not hurt us much.
    root.putChild(b"uri", uri)
    root.putChild(b"api", api)

    return root


def dirnode_json(cap_text, children):
    cap = cap_from_string(cap_text.encode("ascii"))
    info = {
        "verify_uri": cap.get_verify_cap().to_string(),
        "ro_uri": cap.get_readonly().to_string(),
        "children": children,
    }
    if cap.is_mutable():
        info["rw_uri"] = cap.to_string()
    return ["dirnode", info]


def node_json(ro_cap):
    return [
        "filenode",
        {
            "mutable": False,
            "size": 0,
            "ro_uri": ro_cap,
            "metadata": {
                "last_uploaded_uri": ro_cap,
                "deleted": True,
                "user_mtime": 1581972550.542673,
                "version": 1,
                "tahoe": {
                    "linkmotime": 1581972550.578338,
                    "linkcrtime": 1581972248.578481
                },
                "last_downloaded_timestamp": 1581972550.54283,
                "last_downloaded_uri": ro_cap,
            },
            "format": "CHK"
        }
    ]


@attr.s
class StubQueue(object):
    def get_status(self):
        if False:
            yield


@attr.s
class StubMagicFolder(object):
    uploader = attr.ib(default=attr.Factory(StubQueue))
    downloader = attr.ib(default=attr.Factory(StubQueue))
