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
    implementer,
)
from eliot import (
    ActionType,
    MessageType,
    Message,
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
from .snapshot import (
    LocalAuthor,
    LocalSnapshot,
    create_snapshot,
    write_snapshot_to_tahoe,
)
from .status import (
    FolderStatus,
)
from .config import (
    MagicFolderConfig,
)
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
    _cooperator = attr.ib(default=None)

    @inline_callbacks
    def store_local_snapshot(self, path):
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
            parents = []
        else:
            parents = [parent_snapshot]

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
                raw_remote = [parent_remote.danger_real_capability_string()]

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
                input_stream = path.open('rb')
                mtime = int(path.getModificationTime())
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
                    cooperator=self._cooperator,
                )
            finally:
                if input_stream:
                    input_stream.close()

            # store the local snapshot to the database
            self._db.store_local_snapshot(snapshot, path_info.state)
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
        while self.running:
            try:
                (path, d) = yield self._queue.get()
            except CancelledError:
                return

            try:
                with PROCESS_FILE_QUEUE(relpath=path.path):
                    snap = yield self._snapshot_creator.store_local_snapshot(path)
                    d.callback(snap)
            except CancelledError:
                d.cancel()
                return
            except Exception:
                d.errback()
                write_traceback()

    @inline_callbacks
    def stopService(self):
        """
        Don't process queued items anymore.
        """
        yield super(LocalSnapshotService, self).stopService()
        for d in self._queue.waiting:
            d.cancel()  # nobody is getting an answer now
        if self._service_d is not None:
            self._service_d.cancel()
            yield self._service_d
            self._service_d = None

    @log_call_deferred(u"magic-folder:local-snapshots:add-file")
    def add_file(self, path):
        """
        Add the given path of type FilePath to our queue. If the path is
        not strictly below our magic-folder directory, it is an
        error. If the file itself doesn't exist, we process it as a
        'delete'.

        :param FilePath path: path of the file that needs to be
            processed.

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
            # we just want the side-effect here, of confirming that
            # this path is actually inside our magic-path
            path.segmentsFrom(self._config.magic_path)
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
        if path.isdir():
            raise APIError(
                reason=u"expected a regular file, {!r} is a directory".format(path.path),
                code=http.NOT_ACCEPTABLE,
            )

        # add file into the queue
        d = Deferred()
        self._queue.put((path, d))
        return d


@attr.s
@implementer(service.IService)
class UploaderService(service.Service):
    """
    Writes LocalSnapshots to Tahoe
    """
    _config = attr.ib(validator=attr.validators.instance_of(MagicFolderConfig))
    _status = attr.ib(validator=attr.validators.instance_of(FolderStatus))
    _tahoe_client = attr.ib()
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
        while self.running:
            try:
                (snap, d) = yield self._queue.get()
            except CancelledError:
                return

            try:
                remote = yield self._perform_upload(snap)
                d.callback(remote)
            except CancelledError:
                d.cancel()
                return
            except Exception:
                d.errback()
                write_traceback()

    def stopService(self):
        """
        Don't process queued items anymore.
        """
        super(UploaderService, self).stopService()
        d = self._service_d
        self._service_d = None
        if d is not None:
            d.cancel()
            return d

    @inline_callbacks
    def _perform_upload(self, snapshot):
        """
        Do the actual work of performing an upload. Failures are
        propagated to the caller.
        """

        upload_started_at = time.time()
        Message.log(message_type="uploading")

        self._status.upload_started(snapshot.relpath)
        remote_snapshot = yield write_snapshot_to_tahoe(
            snapshot,
            self._config.author,
            self._tahoe_client,
        )
        Message.log(remote_snapshot=remote_snapshot.relpath)
        snapshot.remote_snapshot = remote_snapshot
        yield self._config.store_uploaded_snapshot(
            remote_snapshot.relpath,
            remote_snapshot,
            upload_started_at,
        )
        Message.log(message_type="upload_completed")
        returnValue(remote_snapshot)

    @log_call_deferred(u"magic-folder:local-snapshots:upload_snapshot")
    def upload_snapshot(self, snapshot):
        """
        Queue a snapshot for upload to Tahoe

        :param LocalSnapshot snapshot: the item to upload

        :raises: TypeError if the input is not a LocalSnapshot.
        :returns Deferred[None]: fires when the upload is complete
        """
        if not isinstance(snapshot, LocalSnapshot):
            raise TypeError(
                "argument must be a LocalSnapshot"
            )

        # we could check if this one is already queued -- that should be
        # impossible

        # add file into the queue
        d = Deferred()
        self._queue.put((snapshot, d))
        return d
