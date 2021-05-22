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
from .config import (
    MagicFolderConfig,
)
from . import (
    magicpath,
)


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

    @inline_callbacks
    def store_local_snapshot(self, path):
        """
        Convert `path` into a LocalSnapshot and persist it to disk.

        :param FilePath path: a single file inside our magic-folder dir
        """

        with path.asBytesMode("utf-8").open('rb') as input_stream:
            # Query the db to check if there is an existing local
            # snapshot for the file being added.
            # If so, we use that as the parent.
            mangled_name = magicpath.mangle_path(path)
            try:
                parent_snapshot = self._db.get_local_snapshot(mangled_name)
            except KeyError:
                parents = []
            else:
                parents = [parent_snapshot]

            # need to handle remote-parents when we have remote
            # snapshots

            # when we handle conflicts we will have to handle multiple
            # parents here (or, somewhere)

            relpath_u = path.asTextMode("utf-8").path
            action = SNAPSHOT_CREATOR_PROCESS_ITEM(relpath=relpath_u)
            with action:
                snapshot = yield create_snapshot(
                    name=mangled_name,
                    author=self._author,
                    data_producer=input_stream,
                    snapshot_stash_dir=self._stash_dir.asBytesMode("utf-8"),
                    parents=parents,
                )

                # store the local snapshot to the disk
                self._db.store_local_snapshot(snapshot)

@attr.s
@implementer(service.IService)
class LocalSnapshotService(service.Service):
    """
    When told about local files (that must exist in `.magic_path` or below) we
    deliver it to the snapshot creator.
    """
    _magic_path = attr.ib(
        converter=lambda fp: fp.asBytesMode("utf-8"),
        validator=attr.validators.instance_of(FilePath),
    )
    _snapshot_creator = attr.ib()
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
                (item, d) = yield self._queue.get()
                with PROCESS_FILE_QUEUE(relpath=item.asTextMode('utf-8').path):
                    yield self._snapshot_creator.store_local_snapshot(item.asBytesMode("utf-8"))
                    d.callback(None)
            except CancelledError:
                break
            except Exception:
                # XXX Probably should fire d here, someone might be waiting.
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
        bytespath = path.asBytesMode("utf-8")
        textpath = path.asTextMode("utf-8")

        try:
            bytespath.segmentsFrom(self._magic_path)
        except ValueError:
            ADD_FILE_FAILURE.log(relpath=textpath.path)
            raise ValueError(
                "The path being added '{!r}' is not within '{!r}'".format(
                    bytespath.path,
                    self._magic_path.path,
                )
            )

        # isdir() can fail and can raise an appropriate exception like
        # FileNotFoundError or PermissionError or other filesystem
        # exceptions
        if bytespath.isdir():
            raise ValueError(
                "expected a regular file, {!r} is a directory".format(bytespath.path),
            )

        # add file into the queue
        d = Deferred()
        self._queue.put((bytespath, d))
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

    @inline_callbacks
    def upload_local_snapshots(self):
        """
        Check the db for uncommitted LocalSnapshots, deserialize them from the on-disk
        format to LocalSnapshot objects and commit them into the grid.
        """

        # get the mangled paths for the LocalSnapshot objects in the db
        localsnapshot_names = self._config.get_all_localsnapshot_paths()

        # XXX: processing this table should be atomic. i.e. While the upload is
        # in progress, a new snapshot can be created on a file we already uploaded
        # but not removed from the db and if it gets removed from the table later,
        # the new snapshot gets lost. Perhaps this can be solved by storing each
        # LocalSnapshot in its own row than storing everything in a blob?
        # https://github.com/LeastAuthority/magic-folder/issues/197

        # XXX above comment obsolete, right? (197 is fixed / database is normalized)
        for name in localsnapshot_names:
            action = UPLOADER_SERVICE_UPLOAD_LOCAL_SNAPSHOTS(relpath=name)
            try:
                with action:
                    yield self._upload_some_snapshots(name)
            except Exception:
                # Unable to reach Tahoe storage nodes because of network
                # errors or because the tahoe storage nodes are
                # offline. Retry?
                pass

    @inline_callbacks
    def _upload_some_snapshots(self, name):
        """
        Upload all of the snapshots for a particular path.
        """
        # deserialize into LocalSnapshot
        snapshot = self._config.get_local_snapshot(name)

        remote_snapshot = yield write_snapshot_to_tahoe(
            snapshot,
            self._local_author,
            self._tahoe_client,
        )

        # At this point, remote snapshot creation successful for
        # the given relpath.
        # store the remote snapshot capability in the db.
        yield self._config.store_remotesnapshot(name, remote_snapshot)

        # update the entry in the DMD
        yield self._tahoe_client.add_entry_to_mutable_directory(
            self._upload_dircap.encode("utf-8"),
            name,
            remote_snapshot.capability.encode('utf-8'),
            replace=True,
        )

        # XXX since we've already updated our Personal DMD above, any
        # errors happening below could cause us to be un-synchronized
        # ... if the local stash isn't removed, we waste disk
        # space. If the LocalSnapshot isn't deleted from the databse,
        # we'll try to upload it again (I *think* that's okay, but
        # ...)

        # Remove the local snapshot content from the stash area.
        snapshot.content_path.asBytesMode("utf-8").remove()

        # Remove the LocalSnapshot from the db.
        yield self._config.delete_localsnapshot(name)


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
