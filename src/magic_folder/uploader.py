from __future__ import (
    absolute_import,
    division,
    print_function,
)

import time
import attr

from twisted.python.filepath import (
    FilePath,
)
from twisted.application import (
    service,
)
from twisted.internet.defer import (
    Deferred,
    DeferredQueue,
    CancelledError,
    returnValue,
)
from twisted.web import http
from zope.interface import (
    Interface,
    implementer,
)
from eliot import (
    Message,
    ActionType,
    MessageType,
    write_traceback,
)
from eliot.twisted import (
    inline_callbacks,
)
from .common import APIError
from .util.eliotutil import (
    RELPATH,
    ABSPATH,
    log_call_deferred,
)
from .util.twisted import (
    PeriodicService,
)
from .snapshot import (
    LocalAuthor,
    write_snapshot_to_tahoe,
    create_snapshot,
)
from .status import (
    FolderStatus,
)
from .tahoe_client import (
    TahoeAPIError,
)
from .config import (
    MagicFolderConfig,
)
from .participants import IWriteableParticipant
from .util.file import get_pathinfo


SNAPSHOT_CREATOR_PROCESS_ITEM = ActionType(
    u"magic-folder:local-snapshot-creator:processing-item",
    [RELPATH],
    [],
    u"Local snapshot creator is processing an input.",
)
UPLOADER_SERVICE_UPLOAD_LOCAL_SNAPSHOTS = ActionType(
    u"magic-folder:uploader-service:upload-local-snapshot",
    [RELPATH],
    [],
    u"Uploader service is uploading a local snapshot",
)
SNAPSHOT_COMMIT_FAILURE = MessageType(
    u"magic-folder:uploader-service:snapshot-commit-failure",
    [],
    u"Uploader service is unable to commit the LocalSnapshot.",
)
ADD_FILE_FAILURE = MessageType(
    u"magic-folder:local-snapshot-creator:add-file-failure",
    [ABSPATH],
    u"file path is not a descendent of the magic folder directory",
)
PROCESS_FILE_QUEUE = ActionType(
    u"magic-folder:local-snapshot-creator:process-queue",
    [RELPATH],
    [],
    u"A Magic-Folder is working through an item queue.",
)


@attr.s
class LocalSnapshotCreator(object):
    """
    When given the db and the author instance, this class that actually
    creates a local snapshot and stores it in the database.
    """
    _db = attr.ib()  # our database
    _author = attr.ib(validator=attr.validators.instance_of(LocalAuthor))  # LocalAuthor instance
    _stash_dir = attr.ib(validator=attr.validators.instance_of(FilePath))
    _magic_dir = attr.ib(validator=attr.validators.instance_of(FilePath))
    _tahoe_client = attr.ib()

    @inline_callbacks
    def store_local_snapshot(self, path, local_parent=None):
        """
        Convert `path` into a LocalSnapshot and persist it to disk. If
        `path` does not exist, the this is a 'delete' (and we must
        'know' about the file already).

        :param FilePath path: a single file inside our magic-folder dir

        :returns Deferred[LocalSnapshot]: the completed snapshot
        """
        # Query the db to check if there is an existing local
        # snapshot for the file being added.
        # If so, we use that as the parent.
        relpath = u"/".join(path.segmentsFrom(self._magic_dir))
        try:
            parent_snapshot = self._db.get_local_snapshot(relpath)
        except KeyError:
            parents = [local_parent] if local_parent else []
        else:
            parents = [parent_snapshot]
            if local_parent:
                parents.append(local_parent)

        # if we already have a parent here, it's a LocalSnapshot
        # .. which means that any remote snapshot by definition
        # must be "not our parent" (it should be the parent .. or
        # grandparent etc .. of our localsnapshot)
        raw_remote = []
        try:
            parent_remote = self._db.get_remotesnapshot(relpath)
        except KeyError:
            parent_remote = None
        if parent_remote:
            # XXX should double-check parent relationship if we have any..
            if not parents:
                raw_remote = [parent_remote]

        # when we handle conflicts we will have to handle multiple
        # parents here (or, somewhere)

        action = SNAPSHOT_CREATOR_PROCESS_ITEM(relpath=relpath)
        with action:
            path_info = get_pathinfo(path)

            if not path_info.exists:
                if not raw_remote and not parents:
                    raise Exception(
                        "{} is deleted but no parents found".format(relpath)
                    )

            if path_info.exists:
                input_stream = path.asBytesMode("utf-8").open('rb')
                mtime = int(path.asBytesMode("utf8").getModificationTime())
            else:
                input_stream = None
                mtime = int(time.time())

            try:
                snapshot = yield create_snapshot(
                    relpath=relpath,
                    author=self._author,
                    data_producer=input_stream,
                    snapshot_stash_dir=self._stash_dir,
                    parents=parents,
                    raw_remote_parents=raw_remote,
                    #FIXME from path_info
                    modified_time=mtime,
                )
            finally:
                if input_stream:
                    input_stream.close()

            # store the local snapshot to the disk
            # FIXME: should be in a transaction
            self._db.store_local_snapshot(snapshot)
            self._db.store_currentsnapshot_state(relpath, path_info.state)
            returnValue(snapshot)


@attr.s
@implementer(service.IService)
class LocalSnapshotService(service.Service):
    """
    When told about local files (that must exist in `.magic_path` or below) we
    deliver it to the snapshot creator.
    """
    _config = attr.ib(validator=attr.validators.instance_of(MagicFolderConfig))
    _snapshot_creator = attr.ib()
    _status = attr.ib(validator=attr.validators.instance_of(FolderStatus))
    _queue = attr.ib(default=attr.Factory(DeferredQueue))

    def startService(self):
        """
        Start a periodic loop that looks for work and does it.
        """
        service.Service.startService(self)
        self._service_d = self._process_queue()

    @inline_callbacks
    def _process_queue(self):
        """
        Wait for a single item from the queue and process it, forever.
        """
        while True:
            try:
                (path, d, local_parent) = yield self._queue.get()
                with PROCESS_FILE_QUEUE(relpath=path.path):
                    snap = yield self._snapshot_creator.store_local_snapshot(path, local_parent)
                    d.callback(snap)
            except CancelledError:
                break
            except Exception:
                d.errback()
                write_traceback()

    def stopService(self):
        """
        Don't process queued items anymore.
        """
        d = self._service_d
        self._service_d.cancel()
        service.Service.stopService(self)
        self._service_d = None
        return d

    @log_call_deferred(u"magic-folder:local-snapshots:add-file")
    def add_file(self, path, local_parent=None):
        """
        Add the given path of type FilePath to our queue. If the path is
        not strictly below our magic-folder directory, it is an
        error. If the file itself doesn't exist, we process it as a
        'delete'.

        :param FilePath path: path of the file that needs to be
            processed.

        :param Optional[LocalSnapshot] local_parent: if not specified,
            our most-recent RemoteSnapshot will be the parent. The
            specified LocalSnapshot _should_ have our most-recent
            RemoteSnapshot as one of its parents, though.

        :raises: ValueError if the given file is not a descendent of
                 magic folder path or if the given path is a directory.
        :raises: TypeError if the input is not a FilePath.
        :returns Deferred[LocalSnapshot]: the completed snapshot
        """
        if not isinstance(path, FilePath):
            raise TypeError(
                "argument must be a FilePath"
            )

        try:
            relpath = u"/".join(path.segmentsFrom(self._config.magic_path))
        except ValueError:
            ADD_FILE_FAILURE.log(abspath=path)
            raise APIError(
                reason=u"The path being added '{!r}' is not within '{!r}'".format(
                    path.path,
                    self._config.magic_path.path,
                ),
                code=http.NOT_ACCEPTABLE,
            )

        # isdir() can fail and can raise an appropriate exception like
        # FileNotFoundError or PermissionError or other filesystem
        # exceptions
        if path.asBytesMode('utf-8').isdir():
            raise APIError(
                reason=u"expected a regular file, {!r} is a directory".format(path.path),
                code=http.NOT_ACCEPTABLE,
            )

        # add file into the queue
        d = Deferred()
        self._queue.put((path, d, local_parent))
        return d
