from __future__ import (
    absolute_import,
    division,
    print_function,
)

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
from twisted.internet.task import (
    LoopingCall,
)
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
from .util.eliotutil import (
    RELPATH,
    log_call_deferred,
)
from .snapshot import (
    LocalAuthor,
    write_snapshot_to_tahoe,
    create_snapshot,
)
from .status import (
    IStatus,
)
from .config import (
    MagicFolderConfig,
)
from . import (
    magicpath,
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
            mangled_name = magicpath.path2magic(relpath)

            # when we handle conflicts we will have to handle multiple
            # parents here (or, somewhere)

            action = SNAPSHOT_CREATOR_PROCESS_ITEM(relpath=relpath)
            with action:
                path_info = get_pathinfo(path)

                snapshot = yield create_snapshot(
                    name=mangled_name,
                    author=self._author,
                    data_producer=input_stream,
                    snapshot_stash_dir=self._stash_dir,
                    #FIXME from path_info
                    modified_time=int(path.asBytesMode("utf8").getModificationTime()),
                )
                Message.log(message_type="snapshot", identifier=unicode(snapshot.identifier))

                raise AssertionError(
                    # FIXME
                    """
                    Can we let the database find the parent for us here, or do we need to
                    find it before we start writing the file? My thought was,
                    RemoteSnapshotCreator might upload our parent (turning it
                    from local to remote) while we are stashing the file for
                    our snapshot, rendering the identifier we have invalid.

                    However, there is a possiblity that instead the dowloader
                    is the thing that causes the calculated parent to change.
                    That is unlikely to happen if we get here from the
                    scanner[1], but is quite possible if we get here via the
                    add-snapshot API, and the file is-unchanged from a remote
                    snapshot.

                    I think we may instead need to keep a local-identifier
                    to snapshot capability record (at least in-memory), to
                    deal with the issue instead. The `store_local_snapshot`
                    would need to check that the parent we think we should
                    have is still the current parent.

                    Also, what do we do if the parents no longer match? That
                    would leave us with current remote snapshot that is not an
                    ancestor of the current local snapshot, but with a file on
                    disk that matches the current remote snapshot, not the
                    current local snapshot (assuming no outside changes).

                    [1] It would require (after https://github.com/LeastAuthority/magic-folder/issues/455)
                        that the path state on-disk got reverted to exactly match
                        that of the most recent remote snapshot. This would like require
                        the machine clock to rewind, in-order for ctime (last
                        inode change on unix, file creation time on windows) to be
                        reverted.
                    """
                )
                self._db.store_local_snapshot(mangled_name, snapshot, path_info.state)

@attr.s
@implementer(service.IService)
class LocalSnapshotService(service.Service):
    """
    When told about local files (that must exist in `.magic_path` or below) we
    deliver it to the snapshot creator.
    """
    _magic_path = attr.ib(
        converter=lambda fp: fp.asTextMode("utf-8"),
        validator=attr.validators.instance_of(FilePath),
    )
    _snapshot_creator = attr.ib()
    _status = attr.ib(validator=attr.validators.provides(IStatus))
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
            path.segmentsFrom(self._magic_path)
        except ValueError:
            ADD_FILE_FAILURE.log(relpath=path.path) #FIXME relpath
            raise ValueError(
                "The path being added '{!r}' is not within '{!r}'".format(
                    path.path,
                    self._magic_path.path,
                )
            )

        # isdir() can fail and can raise an appropriate exception like
        # FileNotFoundError or PermissionError or other filesystem
        # exceptions
        if path.asBytesMode('utf-8').isdir():
            raise ValueError(
                "expected a regular file, {!r} is a directory".format(path.path),
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


@implementer(IRemoteSnapshotCreator)
@attr.s
class RemoteSnapshotCreator(object):
    _config = attr.ib(validator=attr.validators.instance_of(MagicFolderConfig))
    _local_author = attr.ib()
    _tahoe_client = attr.ib()
    _upload_dircap = attr.ib()
    _status = attr.ib(validator=attr.validators.provides(IStatus))

    @inline_callbacks
    def upload_local_snapshots(self):
        """
        Check the db for uncommitted LocalSnapshots, deserialize them from the on-disk
        format to LocalSnapshot objects and commit them into the grid.
        """

        # get the mangled paths for the LocalSnapshot objects in the db
        while True:
            uploadable_snapshots = self._config.get_uploadable_snapshots()

            # update our status if we have nothing to do
            if len(uploadable_snapshots):
                self._status.upload_started(self._config.name)
            else:
                self._status.upload_stopped(self._config.name)
                return

            # XXX: processing this table should be atomic. i.e. While the upload is
            # in progress, a new snapshot can be created on a file we already uploaded
            # but not removed from the db and if it gets removed from the table later,
            # the new snapshot gets lost.

            for snapshot in uploadable_snapshots:
                action = UPLOADER_SERVICE_UPLOAD_LOCAL_SNAPSHOTS(relpath=snapshot.name)
                try:
                    with action:
                        yield self._upload_snapshot(snapshot)
                except Exception:
                    # XXX this existing comment is wrong; there are many
                    # reasons we could receive an Exception here not just
                    # "Tahoe is gone" ...
                    # Unable to reach Tahoe storage nodes because of network
                    # errors or because the tahoe storage nodes are
                    # offline. Retry?
                    write_traceback()
                    # We encountered an error, stop trying for now.
                    return

    @inline_callbacks
    def _upload_snapshot(self, snapshot):
        """
        Upload all of the snapshots for a particular path.
        """
        # deserialize into LocalSnapshot
        remote_snapshot = yield write_snapshot_to_tahoe(
            snapshot,
            self._local_author,
            self._tahoe_client,
        )
        Message.log(message_type="snapshot:metadata",
                    local_identifier=unicode(snapshot.identifier),
                    metadata=remote_snapshot.metadata,
                    name=remote_snapshot.name,
                    capability=remote_snapshot.capability)

        # if we crash here, we'll retry and re-upload (hopefully
        # de-duplication works for the content at laest) the
        # Snapshot but no consequences

        # At this point, remote snapshot creation successful for
        # the given relpath.
        # store the remote snapshot capability in the db.
        yield self._config.store_uploaded_snapshot(snapshot, remote_snapshot)

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
        yield self._tahoe_client.add_entry_to_mutable_directory(
            self._upload_dircap.encode("utf-8"),
            snapshot.name,
            remote_snapshot.capability.encode('utf-8'),
            replace=True,
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


@implementer(service.IService)
class UploaderService(service.Service):
    """
    A service that periodically polls the database for local snapshots
    and commit them into the grid.
    """

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

    def __init__(self, clock, poll_interval, remote_snapshot_creator):
        super(UploaderService, self).__init__()

        self._clock = clock
        self._poll_interval = poll_interval
        self._remote_snapshot_creator = remote_snapshot_creator

    def startService(self):
        """
        Start UploaderService and initiate a periodic task
        to poll for LocalSnapshots in the database.
        """

        service.Service.startService(self)

        # do a looping call that polls the db for LocalSnapshots.
        self._processing_loop = LoopingCall(
            self._remote_snapshot_creator.upload_local_snapshots,
        )
        self._processing_loop.clock = self._clock
        self._processing = self._processing_loop.start(self._poll_interval, now=True)

    def stopService(self):
        """
        Stop the uploader service.
        """
        service.Service.stopService(self)
        d = self._processing
        self._processing_loop.stop()
        self._processing = None
        self._processing_loop = None
        return d
