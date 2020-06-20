# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functions and types that implement snapshots
"""
from __future__ import print_function

import io
import os
import time
import json
import base64
from tempfile import mkstemp

import attr
import nacl

from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.web.client import (
    BrowserLikeRedirectAgent,
    FileBodyProducer,
)

from .common import (
    get_node_url,
)
from .magic_folder import (
    load_magic_folders,
    save_magic_folders,
)

from twisted.web.client import (
    readBody,
)

from twisted.web.http import (
    OK,
    CREATED,
)

from hyperlink import (
    DecodedURL,
)

from .common import (
    bad_response,
)

from eliot import (
    start_action,
    register_exception_extractor,
)

from nacl.signing import (
    SigningKey,
    VerifyKey,
)
from nacl.encoding import (
    Base64Encoder,
)

# version of the snapshot scheme
SNAPSHOT_VERSION = 1


@attr.s
class LocalAuthor(object):
    """
    Represents the author of a LocalSnapshot.

    :ivar name: author's name

    :ivar nacl.signing.SigningKey signing_key: author's private key
    """

    name = attr.ib()
    signing_key = attr.ib(validator=[attr.validators.instance_of(SigningKey)])

    # NOTE: this should not be converted to JSON or serialized
    # (because it contains a private key), it is only for signing
    # LocalSnapshot instances (as they're uploaded).

    @property
    def verify_key(self):
        """
        :returns: the VerifyKey corresponding to our signing key
        """
        return self.signing_key.verify_key

    # def to_remote_author(self):
    #     """
    #     :returns: a RemoteAuthor instance. This will be the same, but have
    #         only a verify_key corresponding to our signing_key
    #     """
    #     return create_author(self.name, self.signing_key.verify_key)


def create_local_author(name):
    """
    Create a new local author with a freshly generated private
    (signing) key. This author will not be saved on disk anywhere; see
    `write_local_author` to do that.

    :param name: the name of this author
    """
    signing_key = SigningKey.generate()
    return LocalAuthor(
        name,
        signing_key,
    )


def write_local_author(local_author, magic_folder_name, config):
    """
    Writes a LocalAuthor instance beside other magic-folder data in the node-directory
    """
    key_fname = "magicfolder_{}.privkey".format(magic_folder_name)
    path = config.get_config_path("private", key_fname)
    keydata_base64 = local_author.signing_key.encode(encoder=Base64Encoder)
    author_data = {
        "author_name": local_author.name,
        "author_private_key": keydata_base64,
    }
    with open(path, "w") as f:
        json.dump(author_data, f)


def create_local_author_from_config(config, name=None):
    """
    :param config: a Tahoe config instance (created via `allmydata.client.read_config`)

    :param name: which Magic Folder to use (or 'default')

    :returns: a LocalAuthor instance from our configuration
    """
    # private-keys go in "<node_dir>/private/magicfolder_<name>.privkey"
    # to mirror where the sqlite database goes
    if name is None:
        name = "default"
    nodedir = config.get_config_path()
    magic_folders = load_magic_folders(nodedir)
    if name not in magic_folders:
        raise RuntimeError(
            "No magic-folder named '{}'".format(name)
        )

    # we will always have authorship information for this
    # magic-folder; "legacy" magic-folders will go through "tahoe
    # migrate" first and have an author created.

    author_raw = config.get_private_config("magicfolder_{}.privkey".format(name))
    author_data = json.loads(author_raw)

    return LocalAuthor(
        name=author_data[u"author_name"],
        signing_key=SigningKey(
            author_data[u"author_private_key"],
            encoder=Base64Encoder,
        ),
    )


@attr.s
class LocalSnapshot(object):
    name = attr.ib()
    author = attr.ib()
    metadata = attr.ib()
    content_path = attr.ib()  # full filesystem path to our stashed contents
    parents_local = attr.ib()  # LocalSnapshot instances

    # once we do uploads / downloads and have RemoteSnapshots, we will
    # also have those kind of parents too:
    # parents_remote = attr.ib()
    # ..and need to add methods here to count and async get parents

    def get_content_producer(self):
        """
        :returns: an IBodyProducer that gives you all the bytes of the
            on-disc content. Raises an error if we already have a
            capability. Note that this data will have been stashed previously.
        """
        return FileBodyProducer(
            open(self.content_path, "rb")
        )


@inlineCallbacks
def create_snapshot(name, author, data_producer, snapshot_stash_dir, parents=None):
    """
    Creates a new LocalSnapshot instance that is in-memory only. All
    data is stashed in `snapshot_stash_dir` before this function
    returns.

    :param author: LocalAuthor instance (which will have a valid
        signing-key)

    :param data_producer: file-like object that can deliver all the
        data for the content of this Snapshot (it will be read
        immediately).

    :param snapshot_stash_dir: the directory where Snapshot contents
        are to be stashed.

    :param parents: a list of LocalSnapshot instances (may be empty,
        which is the default if not specified).
    """
    if parents is None:
        parents = []
    yield

    if not isinstance(author, LocalAuthor):
        raise ValueError(
            "create_snapshot 'author' must be a LocalAuthor instance"
        )

    # when we do uploads, we will distinguish between remote and local
    # parents, so the "parents" list may contain either kind in the
    # future.
    parents_local = []
    for idx, parent in enumerate(parents_local):
        if isinstance(parent, LocalSnapshot):
            parents_local.append(parent)
        else:
            raise ValueError(
                "Parent {} is type {} not LocalSnapshot".format(
                    idx,
                    type(parent),
                )
            )

    chunk_size = 1024*1024  # 1 MiB
    chunks_per_yield = 100

    # 1. create a temp-file in our stash area
    temp_file_fd, temp_file_name = mkstemp(
        prefix="snap",
        dir=snapshot_stash_dir,
    )
    try:
        # 2. stream data_producer into our temp-file
        done = False
        while not done:
            for _ in range(chunks_per_yield):
                data = data_producer.read(chunk_size)
                if data:
                    if len(data) > 0:
                        os.write(temp_file_fd, data)
                else:
                    done = True
                    break
    finally:
        os.close(temp_file_fd)

    now = time.time()
    returnValue(
        LocalSnapshot(
            name=name,
            author=author,
            metadata={
                "ctime": now,
                "mtime": now,
            },
            content_path=temp_file_name,
            parents_local=parents_local,
        )
    )
