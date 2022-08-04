from twisted.python.filepath import FilePath, InsecurePath
from twisted.internet import defer
from twisted.internet.defer import (
    Deferred,
)
from twisted.application import service
from twisted.web import http

from eliot import (
    Field,
    ActionType,
    MessageType,
)
from eliot.twisted import (
    inline_callbacks,
)

from .common import APIError
from .util.eliotutil import (
    RELPATH,
    validateSetMembership,
    validateInstanceOf,
)

from .uploader import (
    LocalSnapshotService,
    LocalSnapshotCreator,
    UploaderService,
)
from .downloader import (
    RemoteSnapshotCacheService,
    RemoteScannerService,
    LocalMagicFolderFilesystem,
)
from .magic_file import (
    MagicFileFactory,
)
from .participants import (
    IParticipant,
    participants_from_collective,
)
from .scanner import (
    ScannerService,
)
from .status import FolderStatus


# Mask off all non-owner permissions for magic-folders files by default.
_DEFAULT_DOWNLOAD_UMASK = 0o077

IN_EXCL_UNLINK = int(0x04000000)


class MagicFolder(service.MultiService):
    """
    :ivar LocalSnapshotService local_snapshot_service: A child service
        responsible for creating new local snapshots for files in this folder.
    """

    @classmethod
    def from_config(cls, reactor, tahoe_client, name, config, status_service, cooperator=None):
        """
        Create a ``MagicFolder`` from a client node and magic-folder
        configuration.

        :param IReactorTime reactor: the reactor to use

        :param magic_folder.cli.TahoeClient tahoe_client: Access the API of
            the Tahoe-LAFS client we're associated with.

        :param GlobalConfigurationDatabase config: our configuration

        :param IStatus status_service: status-reporting service

        :param Cooperator cooperator: a cooperator to use for child
            services (or None for the Twisted global default cooperator).
        """
        mf_config = config.get_magic_folder(name)

        folder_status = FolderStatus(name, status_service)

        participants = participants_from_collective(
            mf_config.collective_dircap,
            # FIXME: verify this corresponds to collective dircap
            # with the right name
            mf_config.upload_dircap,
            tahoe_client
        )

        remote_snapshot_cache_service = RemoteSnapshotCacheService.from_config(
            config=mf_config,
            tahoe_client=tahoe_client,
        )
        local_snapshot_service = LocalSnapshotService(
            mf_config,
            LocalSnapshotCreator(
                mf_config,
                mf_config.author,
                mf_config.stash_path,
                mf_config.magic_path,
                tahoe_client,
                cooperator=cooperator,
            ),
            status=folder_status,
        )
        uploader = UploaderService(
            mf_config,
            folder_status,
            tahoe_client,
        )
        magic_file_factory = MagicFileFactory(
            mf_config,
            tahoe_client,
            folder_status,
            local_snapshot_service,
            uploader,
            participants.writer,
            remote_snapshot_cache_service,
            LocalMagicFolderFilesystem(
                mf_config.magic_path,
                mf_config.stash_path,
            ),
        )
        scanner_service = ScannerService.from_config(
            reactor,
            mf_config,
            magic_file_factory,
            folder_status,
        )

        return cls(
            client=tahoe_client,
            config=mf_config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            remote_snapshot_cache=remote_snapshot_cache_service,
            downloader=RemoteScannerService.from_config(
                clock=reactor,
                name=name,
                config=mf_config,
                participants=participants,
                file_factory=magic_file_factory,
                remote_snapshot_cache=remote_snapshot_cache_service,
                status_service=status_service,
            ),
            uploader=uploader,
            folder_status=folder_status,
            scanner_service=scanner_service,
            participants=participants,
            clock=reactor,
            magic_file_factory=magic_file_factory,
        )

    @property
    def name(self):
        # this is used by 'service' things and must be unique in this Service hierarchy
        return u"magic-folder-{}".format(self.folder_name)

    def __init__(self, client, config, name, local_snapshot_service, folder_status, scanner_service, remote_snapshot_cache, downloader, uploader, participants, clock, magic_file_factory):
        super(MagicFolder, self).__init__()
        self.folder_name = name
        self._clock = clock
        self.config = config  # a MagicFolderConfig instance
        self._participants = participants
        self.file_factory = magic_file_factory
        self.local_snapshot_service = local_snapshot_service
        self.downloader_service = downloader
        self.uploader_service = uploader
        self.folder_status = folder_status
        self.scanner_service = scanner_service
        # By setting the parents these services will now start when
        # self, the top-level service, starts
        local_snapshot_service.setServiceParent(self)
        downloader.setServiceParent(self)
        uploader.setServiceParent(self)
        scanner_service.setServiceParent(self)

    @inline_callbacks
    def stopService(self):
        yield self.file_factory.cancel()
        yield super(MagicFolder, self).stopService()

    def ready(self):
        """
        :returns: Deferred that fires with None when this magic-folder is
            ready to operate
        """
        return defer.succeed(None)

    def scan_local(self):
        """
        Scan the magic folder for changes.

        :returns Deferred[None]: that fires when all the changed files have
            been snapshotted.
        """
        return self.scanner_service.scan_once()

    def poll_remote(self):
        """
        Poll the Collective DMD for remote changes.

        :returns Deferred[None]: that fires when the polling is complete
        """
        return self.downloader_service.poll_once()

    def participants(self):
        # type: () -> Deferred[list[IParticipant]]
        """
        List all participants of this folder
        """
        return self._participants.list()

    def add_participant(self, author, participant_directory):
        return self._participants.add(author, participant_directory)

    def add_snapshot(self, relative_path):
        # type: (str) -> Deferred[None]
        """
        Create a new snapshot of the given file.

        :returns Deferred: fires (with None) after the local state has
            been serialized to the database.
        """

        # preauthChild allows path-separators in the "path" (i.e. not
        # just a single path-segment). That is precisely what we want
        # here, though. It sill does not allow the path to "jump out"
        # of the base magic_path -- that is, an InsecurePath error
        # will result if you pass an absolute path outside the folder
        # or a relative path that reaches up too far.
        try:
            path = self.config.magic_path.preauthChild(relative_path)
        except InsecurePath as e:
            return defer.fail(
                APIError.from_exception(http.NOT_ACCEPTABLE, e)
            )
        mf = self.file_factory.magic_file_for(path)
        d = mf.create_update()
        return d


_NICKNAME = Field.for_types(
    u"nickname",
    [str, bytes],
    u"A Magic-Folder participant nickname.",
)

_DIRECTION = Field.for_types(
    u"direction",
    [str],
    u"A synchronization direction: uploader or downloader.",
    validateSetMembership({u"uploader", u"downloader"}),
)

PROCESSING_LOOP = ActionType(
    u"magic-folder:processing-loop",
    [_NICKNAME, _DIRECTION],
    [],
    u"A Magic-Folder is processing uploads or downloads.",
)

ITERATION = ActionType(
    u"magic-folder:iteration",
    [_NICKNAME, _DIRECTION],
    [],
    u"A step towards synchronization in one direction.",
)

_COUNT = Field.for_types(
    u"count",
    [int],
    u"The number of items in the processing queue.",
)

PROCESS_QUEUE = ActionType(
    u"magic-folder:process-queue",
    [_COUNT],
    [],
    u"A Magic-Folder is working through an item queue.",
)

SCAN_REMOTE_COLLECTIVE = ActionType(
    u"magic-folder:scan-remote-collective",
    [],
    [],
    u"The remote collective is being scanned for peer DMDs.",
)

_DMDS = Field(
    u"dmds",
    lambda participants: list(participant.name for participant in participants),
    u"The (D)istributed (M)utable (D)irectories belonging to each participant are being scanned for changes.",
)

COLLECTIVE_SCAN = MessageType(
    u"magic-folder:downloader:get-latest-file:collective-scan",
    [_DMDS],
    u"Participants in the collective are being scanned.",
)


SCAN_REMOTE_DMD = ActionType(
    u"magic-folder:scan-remote-dmd",
    [_NICKNAME],
    [],
    u"A peer DMD is being scanned for changes.",
)

REMOTE_VERSION = Field.for_types(
    u"remote_version",
    [int],
    u"The version of a path found in a peer DMD.",
)

REMOTE_URI = Field.for_types(
    u"remote_uri",
    [bytes],
    u"The filecap of a path found in a peer DMD.",
)

ADD_TO_DOWNLOAD_QUEUE = MessageType(
    u"magic-folder:add-to-download-queue",
    [RELPATH],
    u"An entry was found to be changed and is being queued for download.",
)

MAGIC_FOLDER_STOP = ActionType(
    u"magic-folder:stop",
    [_NICKNAME],
    [],
    u"A Magic-Folder is being stopped.",
)

MAYBE_UPLOAD = MessageType(
    u"magic-folder:maybe-upload",
    [RELPATH],
    u"A decision is being made about whether to upload a file.",
)

PENDING = Field(
    u"pending",
    lambda s: list(s),
    u"The paths which are pending processing.",
    validateInstanceOf(set),
)

REMOVE_FROM_PENDING = ActionType(
    u"magic-folder:remove-from-pending",
    [RELPATH, PENDING],
    [],
    u"An item being processed is being removed from the pending set.",
)

PATH = Field(
    u"path",
    lambda fp: "<None>" if fp is None else fp.path,
    u"A local filesystem path.",
    validateInstanceOf(FilePath),
)

NOTIFIED_OBJECT_DISAPPEARED = MessageType(
    u"magic-folder:notified-object-disappeared",
    [PATH],
    u"A path which generated a notification was not found on the filesystem.  This is normal.",
)

PROPAGATE_DIRECTORY_DELETION = ActionType(
    u"magic-folder:propagate-directory-deletion",
    [],
    [],
    u"Children of a deleted directory are being queued for upload processing.",
)

NO_DATABASE_ENTRY = MessageType(
    u"magic-folder:no-database-entry",
    [],
    u"There is no local database entry for a particular relative path in the magic folder.",
)

NOT_UPLOADING = MessageType(
    u"magic-folder:not-uploading",
    [],
    u"An item being processed is not going to be uploaded.",
)

SYMLINK = MessageType(
    u"magic-folder:symlink",
    [PATH],
    u"An item being processed was a symlink and is being skipped",
)

CREATED_DIRECTORY = Field.for_types(
    u"created_directory",
    [str],
    u"The relative path of a newly created directory in a magic-folder.",
)

PROCESS_DIRECTORY = ActionType(
    u"magic-folder:process-directory",
    [],
    [CREATED_DIRECTORY],
    u"An item being processed was a directory.",
)

NOT_NEW_DIRECTORY = MessageType(
    u"magic-folder:not-new-directory",
    [],
    u"A directory item being processed was found to not be new.",
)

NOT_NEW_FILE = MessageType(
    u"magic-folder:not-new-file",
    [],
    u"A file item being processed was found to not be new (or changed).",
)

SPECIAL_FILE = MessageType(
    u"magic-folder:special-file",
    [],
    u"An item being processed was found to be of a special type which is not supported.",
)

_COUNTER_NAME = Field.for_types(
    u"counter_name",
    # Should really only be unicode
    [str, bytes],
    u"The name of a counter.",
)

_DELTA = Field.for_types(
    u"delta",
    [int],
    u"An amount of a specific change in a counter.",
)

_VALUE = Field.for_types(
    u"value",
    [int],
    u"The new value of a counter after a change.",
)

COUNT_CHANGED = MessageType(
    u"magic-folder:count",
    [_COUNTER_NAME, _DELTA, _VALUE],
    u"The value of a counter has changed.",
)

_IGNORED = Field.for_types(
    u"ignored",
    [bool],
    u"A file proposed for queueing for processing is instead being ignored by policy.",
)

_ALREADY_PENDING = Field.for_types(
    u"already_pending",
    [bool],
    u"A file proposed for queueing for processing is already in the queue.",
)

_SIZE = Field.for_types(
    u"size",
    [int, type(None)],
    u"The size of a file accepted into the processing queue.",
)

_ABSPATH = Field.for_types(
    u"abspath",
    [str],
    u"The absolute path of a file being written in a local directory.",
)

_IS_CONFLICT = Field.for_types(
    u"is_conflict",
    [bool],
    u"An indication of whether a file being written in a local directory is in a conflicted state.",
)

_NOW = Field.for_types(
    u"now",
    [int, float],
    u"The time at which a file is being written in a local directory.",
)

_MTIME = Field.for_types(
    u"mtime",
    [int, float, type(None)],
    u"A modification time to put into the metadata of a file being written in a local directory.",
)

WRITE_DOWNLOADED_FILE = ActionType(
    u"magic-folder:write-downloaded-file",
    [_ABSPATH, _SIZE, _IS_CONFLICT, _NOW, _MTIME],
    [],
    u"A downloaded file is being written to the filesystem.",
)

ALREADY_GONE = MessageType(
    u"magic-folder:rename:already-gone",
    [],
    u"A deleted file could not be rewritten to a backup path because it no longer exists.",
)

_REASON = Field(
    u"reason",
    lambda e: str(e),
    u"An exception which may describe the form of the conflict.",
    validateInstanceOf(Exception),
)

OVERWRITE_BECOMES_CONFLICT = MessageType(
    u"magic-folder:overwrite-becomes-conflict",
    [_REASON],
    u"An attempt to overwrite an existing file failed because that file is now conflicted.",
)

_FILES = Field(
    u"files",
    lambda file_set: list(file_set),
    u"All of the relative paths belonging to a Magic-Folder that are locally known.",
)

ALL_FILES = MessageType(
    u"magic-folder:all-files",
    [_FILES],
    u"A record of the rough state of the local database at the time of downloader start up.",
)

_ITEMS = Field(
    u"items",
    lambda deque: list(dict(relpath=item.relpath_u, kind=item.kind) for item in deque),
    u"Items in a processing queue.",
)

ITEM_QUEUE = MessageType(
    u"magic-folder:item-queue",
    [_ITEMS],
    u"A report of the items in the processing queue at this point.",
)

_BATCH = Field(
    u"batch",
    # Just report the paths for now.  Perhaps something from the values would
    # also be useful, though?  Consider it.
    lambda batch: batch.keys(),
    u"A batch of scanned items.",
    validateInstanceOf(dict),
)

SCAN_BATCH = MessageType(
    u"magic-folder:scan-batch",
    [_BATCH],
    u"Items in a batch of files which were scanned from the DMD.",
)

START_DOWNLOADING = ActionType(
    u"magic-folder:start-downloading",
    [_NICKNAME, _DIRECTION],
    [],
    u"A Magic-Folder downloader is initializing and beginning to manage downloads.",
)

PERFORM_SCAN = ActionType(
    u"magic-folder:perform-scan",
    [],
    [],
    u"Remote storage is being scanned for changes which need to be synchronized.",
)

_CONFLICT_REASON = Field.for_types(
    u"conflict_reason",
    [str, type(None)],
    u"A human-readable explanation of why a file was in conflict.",
    validateSetMembership({
        u"dbentry mismatch metadata",
        u"dbentry newer version",
        u"last_downloaded_uri mismatch",
        u"file appeared",
        None,
    }),
)

CHECKING_CONFLICTS = ActionType(
    u"magic-folder:item:checking-conflicts",
    [],
    [_IS_CONFLICT, _CONFLICT_REASON],
    u"A potential download item is being checked to determine if it is in a conflicted state.",
)

REMOTE_DIRECTORY_CREATED = MessageType(
    u"magic-folder:remote-directory-created",
    [],
    u"The downloader found a new directory in the DMD.",
)

REMOTE_DIRECTORY_DELETED = MessageType(
    u"magic-folder:remote-directory-deleted",
    [],
    u"The downloader found a directory has been deleted from the DMD.",
)
