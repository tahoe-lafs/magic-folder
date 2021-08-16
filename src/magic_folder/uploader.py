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
    [RELPATH],
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
    def store_local_snapshot(self, path):
        """
        Convert `path` into a LocalSnapshot and persist it to disk.

        :param FilePath path: a single file inside our magic-folder dir
        """
        # TODO: We may to have logic similar to (shared with?) the scanner,
        # to see if we need to snapshot the file. This is probably most useful
        # when we get here via API, rather than the scanner, but may also avoid
        # duplicate snapshots if scanning doesn't wait for snapshotting to
        # complete.

        with path.asBytesMode("utf-8").open('rb') as input_stream:
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
            if not parents:
                try:
                    parent_remote = self._db.get_remotesnapshot(relpath)
                    raw_remote = [parent_remote]
                except KeyError:
                    pass

            # when we handle conflicts we will have to handle multiple
            # parents here (or, somewhere)

            action = SNAPSHOT_CREATOR_PROCESS_ITEM(relpath=relpath)
            with action:
                path_info = get_pathinfo(path)

                snapshot = yield create_snapshot(
                    relpath=relpath,
                    author=self._author,
                    data_producer=input_stream,
                    snapshot_stash_dir=self._stash_dir,
                    parents=parents,
                    raw_remote_parents=raw_remote,
                    #FIXME from path_info
                    modified_time=int(path.asBytesMode("utf8").getModificationTime()),
                )

                # store the local snapshot to the disk
                # FIXME: should be in a transaction
                self._db.store_local_snapshot(snapshot)
                self._db.store_currentsnapshot_state(relpath, path_info.state)

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
    _uploader_service = attr.ib()
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
                (path, d) = yield self._queue.get()
                with PROCESS_FILE_QUEUE(relpath=path.path):
                    yield self._snapshot_creator.store_local_snapshot(path)
                    # We explicitly don't wait to upload the snapshot.
                    self._uploader_service.perform_upload()
                    d.callback(None)
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
    def add_file(self, path):
        """
        Add the given path of type FilePath to our queue. If the path
        does not exist below our magic-folder directory, it is an error.

        :param FilePath path: path of the file that needs to be added.

        :raises: ValueError if the given file is not a descendent of
                 magic folder path or if the given path is a directory.
        :raises: TypeError if the input is not a FilePath.
        """
        if not isinstance(path, FilePath):
            raise TypeError(
                "argument must be a FilePath"
            )

        try:
            # check that "path" is a descendant of magic_path
            relpath = u"/".join(path.segmentsFrom(self._config.magic_path))
            self._status.upload_queued(relpath)
        except ValueError:
            ADD_FILE_FAILURE.log(relpath=path.path) #FIXME relpath
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
        self._queue.put((path, d))
        return d


class IRemoteSnapshotCreator(Interface):
    """
    An object that can create remote snapshots representing the same
    information as some local snapshots that already exist.
    """
    def upload_local_snapshots():
        """
        Find local uncommitted snapshots and commit them to the grid.

        :return Deferred[None]: A Deferred that fires when an effort has been
            made to commit at least some local uncommitted snapshots.
        """

    def initialize_upload_status(self):
        """
        Called precisely once upon startup, before any calls to
        upload_local_snapshots.
        """


@implementer(IRemoteSnapshotCreator)
@attr.s
class RemoteSnapshotCreator(object):
    _config = attr.ib(validator=attr.validators.instance_of(MagicFolderConfig))
    _local_author = attr.ib()
    _tahoe_client = attr.ib()
    _write_participant = attr.ib(validator=attr.validators.provides(IWriteableParticipant))
    _status = attr.ib(validator=attr.validators.instance_of(FolderStatus))

    def initialize_upload_status(self):
        """
        If any local-snapshots are present in our database at
        startup we need to reflect their "pending upload" status
        properly.
        """
        localsnapshot_paths = self._config.get_all_localsnapshot_paths()
        for relpath in localsnapshot_paths:
            # if we re-started with LocalSnapshots already in our database
            # locally, we won't have done a .upload_queued() yet _in this
            # process_ (that is, a previous daemon did that resulting in
            # the database entries)
            self._status.upload_queued(relpath)

    @inline_callbacks
    def upload_local_snapshots(self):
        """
        Check the db for uncommitted LocalSnapshots, deserialize them from the on-disk
        format to LocalSnapshot objects and commit them into the grid.
        """
        # get the paths for the LocalSnapshot objects in the db
        localsnapshot_paths = self._config.get_all_localsnapshot_paths()

        # XXX: processing this table should be atomic. i.e. While the upload is
        # in progress, a new snapshot can be created on a file we already uploaded
        # but not removed from the db and if it gets removed from the table later,
        # the new snapshot gets lost.

        for relpath in localsnapshot_paths:
            action = UPLOADER_SERVICE_UPLOAD_LOCAL_SNAPSHOTS(relpath=relpath)
            try:
                with action:
                    self._status.upload_started(relpath)
                    yield self._upload_some_snapshots(relpath)
            except TahoeAPIError as e:
                self._status.error_occurred(u"Failed to upload to Tahoe. code={}".format(e.code))
                write_traceback()
                # note: we will re-try on the next upload pass, after one scan_interval
            except Exception:
                write_traceback()
            else:
                self._status.upload_finished(relpath)

    @inline_callbacks
    def _upload_some_snapshots(self, relpath):
        """
        Upload all of the snapshots for a particular path.
        """
        # deserialize into LocalSnapshot
        snapshot = self._config.get_local_snapshot(relpath)
        upload_started_at = time.time()
        remote_snapshot = yield write_snapshot_to_tahoe(
            snapshot,
            self._local_author,
            self._tahoe_client,
        )
        Message.log(message_type="snapshot:metadata",
                    metadata=remote_snapshot.metadata,
                    relpath=relpath,
                    capability=remote_snapshot.capability)

        # if we crash here, we'll retry and re-upload (hopefully
        # de-duplication works for the content at laest) the
        # Snapshot but no consequences

        # At this point, remote snapshot creation successful for
        # the given relpath.
        # store the remote snapshot capability in the db.
        yield self._config.store_uploaded_snapshot(relpath, remote_snapshot, upload_started_at)

        # if we crash here, there's an inconsistency between our
        # remote and local state: we believe the version is X but
        # other participants see X<-ancestor-Y (that is, version Y
        # that has X as an ancestor) .. we will re-try the whole
        # operation and get back here. The Downloader may be confused
        # but it should find "X" the common ancestor even if another
        # version X<--Y<--Z is published (and hence properly see it as
        # an overwrite). If we create a new LocalSnapshot while
        # inconsistent we will note the parent as X when it was really
        # the _content_ from parent Y .. we'll still I think correctly
        # detect the conflict but any diff migh be weird.

        # update the entry in the DMD
        yield self._write_participant.update_snapshot(
            relpath,
            remote_snapshot.capability,
        )

        # if removing the stashed content fails here, we MUST move on
        # to delete the LocalSnapshot because we may not be able to
        # re-create the Snapshot (maybe the content is "partially
        # deleted"?
        try:
            # Remove the local snapshot content from the stash area.
            snapshot.content_path.asBytesMode("utf-8").remove()
        except Exception as e:
            print(
                "Failed to remove cache: '{}': {}".format(
                    snapshot.content_path.asTextMode("utf-8").path,
                    str(e),
                )
            )

        # Remove the LocalSnapshot from the db.
        yield self._config.delete_localsnapshot(relpath)


@attr.s
class UploaderService(service.MultiService):
    """
    A service that periodically polls the database for local snapshots
    and commit them into the grid.
    """
    _clock = attr.ib()
    _poll_interval = attr.ib()
    _remote_snapshot_creator = attr.ib()
    _periodic_service = attr.ib()

    @_periodic_service.default
    def _init_periodic(self):
        return PeriodicService(
            clock=self._clock,
            interval=self._poll_interval,
            callable=self._remote_snapshot_creator.upload_local_snapshots,
        )

    @classmethod
    def from_config(cls, clock, config, remote_snapshot_creator):
        """
        Create an UploaderService from the MagicFolder configuration.
        """
        mf_config = config
        poll_interval = mf_config.poll_interval
        return cls(
            clock=clock,
            poll_interval=poll_interval,
            remote_snapshot_creator=remote_snapshot_creator,
        )

    def __attrs_post_init__(self):
        super(UploaderService, self).__init__()
        self._periodic_service.setServiceParent(self)

    def startService(self):
        """
        Start UploaderService and initiate a periodic task
        to poll for LocalSnapshots in the database.
        """
        super(UploaderService, self).startService()

        # if we started with any local snapshots already in the
        # database we must reflect this in our status service.
        self._remote_snapshot_creator.initialize_upload_status()

    def perform_upload(self):
        """
        Do an upload unless we are already doing one.
        """
        self._periodic_service.call_soon()
