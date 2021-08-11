from __future__ import absolute_import, division, print_function, unicode_literals

import six
from eliot import ActionType, Field, MessageType
from twisted.application import service
from twisted.internet import defer
from twisted.internet.defer import Deferred
from twisted.python.filepath import FilePath, InsecurePath
from twisted.web import http

from .common import APIError
from .downloader import (
    DownloaderService,
    LocalMagicFolderFilesystem,
    MagicFolderUpdater,
    RemoteSnapshotCacheService,
)
from .participants import IParticipant, participants_from_collective
from .scanner import ScannerService
from .status import FolderStatus
from .uploader import (
    LocalSnapshotCreator,
    LocalSnapshotService,
    RemoteSnapshotCreator,
    UploaderService,
)
from .util.eliotutil import RELPATH, validateInstanceOf, validateSetMembership

if six.PY3:
    long = int


# Mask off all non-owner permissions for magic-folders files by default.
_DEFAULT_DOWNLOAD_UMASK = 0o077

IN_EXCL_UNLINK = long(0x04000000)


class MagicFolder(service.MultiService):
    """
    :ivar LocalSnapshotService local_snapshot_service: A child service
        responsible for creating new local snapshots for files in this folder.
    """

    @classmethod
    def from_config(cls, reactor, tahoe_client, name, config, status_service):
        """
        Create a ``MagicFolder`` from a client node and magic-folder
        configuration.

        :param IReactorTime reactor: the reactor to use

        :param magic_folder.cli.TahoeClient tahoe_client: Access the API of
            the Tahoe-LAFS client we're associated with.

        :param GlobalConfigurationDatabase config: our configuration
        """
        mf_config = config.get_magic_folder(name)

        folder_status = FolderStatus(name, status_service)

        participants = participants_from_collective(
            mf_config.collective_dircap,
            # FIXME: verify this corresponds to collective dircap
            # with the right name
            mf_config.upload_dircap,
            tahoe_client,
        )

        remote_snapshot_cache_service = RemoteSnapshotCacheService.from_config(
            config=mf_config,
            tahoe_client=tahoe_client,
        )
        uploader_service = UploaderService.from_config(
            clock=reactor,
            config=mf_config,
            remote_snapshot_creator=RemoteSnapshotCreator(
                config=mf_config,
                local_author=mf_config.author,
                tahoe_client=tahoe_client,
                write_participant=participants.writer,
                status=folder_status,
            ),
        )
        local_snapshot_service = LocalSnapshotService(
            mf_config,
            LocalSnapshotCreator(
                mf_config,
                mf_config.author,
                mf_config.stash_path,
                mf_config.magic_path,
                tahoe_client,
            ),
            status=folder_status,
            uploader_service=uploader_service,
        )
        scanner_service = ScannerService.from_config(
            reactor,
            mf_config,
            local_snapshot_service,
            status=folder_status,
        )

        return cls(
            client=tahoe_client,
            config=mf_config,
            name=name,
            local_snapshot_service=local_snapshot_service,
            uploader_service=uploader_service,
            remote_snapshot_cache=remote_snapshot_cache_service,
            downloader=DownloaderService.from_config(
                name=name,
                config=mf_config,
                participants=participants,
                status=folder_status,
                remote_snapshot_cache=remote_snapshot_cache_service,
                folder_updater=MagicFolderUpdater(
                    reactor,
                    LocalMagicFolderFilesystem(
                        mf_config.magic_path,
                        mf_config.stash_path,
                    ),
                    mf_config,
                    remote_snapshot_cache_service,
                    tahoe_client,
                    status=folder_status,
                    write_participant=participants.writer,
                ),
                tahoe_client=tahoe_client,
            ),
            folder_status=folder_status,
            scanner_service=scanner_service,
            participants=participants,
            clock=reactor,
        )

    @property
    def name(self):
        # this is used by 'service' things and must be unique in this Service hierarchy
        return "magic-folder-{}".format(self.folder_name)

    def __init__(
        self,
        client,
        config,
        name,
        local_snapshot_service,
        uploader_service,
        folder_status,
        scanner_service,
        remote_snapshot_cache,
        downloader,
        participants,
        clock,
    ):
        super(MagicFolder, self).__init__()
        self.folder_name = name
        self._clock = clock
        self.config = config  # a MagicFolderConfig instance
        self._participants = participants
        self.local_snapshot_service = local_snapshot_service
        self.uploader_service = uploader_service
        self.downloader_service = downloader
        self.folder_status = folder_status
        self.scanner_service = scanner_service
        # By setting the parents these services will now start when
        # self, the top-level service, starts
        local_snapshot_service.setServiceParent(self)
        uploader_service.setServiceParent(self)
        downloader.setServiceParent(self)
        local_snapshot_service.setServiceParent(self)
        uploader_service.setServiceParent(self)
        scanner_service.setServiceParent(self)

    def ready(self):
        """
        :returns: Deferred that fires with None when this magic-folder is
            ready to operate
        """
        return defer.succeed(None)

    def scan(self):
        """
        Scan the magic folder for changes.

        :returns Deferred[None]: that fires when all the changed files have
            been snapshotted.
        """
        return self.scanner_service.scan_once()

    def participants(self):
        # type: () -> Deferred[list[IParticipant]]
        """
        List all participants of this folder
        """
        return self._participants.list()

    def add_participant(self, author, participant_directory):
        return self._participants.add(author, participant_directory)

    def add_snapshot(self, relative_path):
        # type: (unicode) -> Deferred[None]
        """
        Create a new snapshot of the given file.
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
            return defer.fail(APIError.from_exception(http.NOT_ACCEPTABLE, e))
        return self.local_snapshot_service.add_file(path)


_NICKNAME = Field.for_types(
    "nickname",
    [unicode, bytes],
    "A Magic-Folder participant nickname.",
)

_DIRECTION = Field.for_types(
    "direction",
    [unicode],
    "A synchronization direction: uploader or downloader.",
    validateSetMembership({"uploader", "downloader"}),
)

PROCESSING_LOOP = ActionType(
    "magic-folder:processing-loop",
    [_NICKNAME, _DIRECTION],
    [],
    "A Magic-Folder is processing uploads or downloads.",
)

ITERATION = ActionType(
    "magic-folder:iteration",
    [_NICKNAME, _DIRECTION],
    [],
    "A step towards synchronization in one direction.",
)

_COUNT = Field.for_types(
    "count",
    [int, long],
    "The number of items in the processing queue.",
)

PROCESS_QUEUE = ActionType(
    "magic-folder:process-queue",
    [_COUNT],
    [],
    "A Magic-Folder is working through an item queue.",
)

SCAN_REMOTE_COLLECTIVE = ActionType(
    "magic-folder:scan-remote-collective",
    [],
    [],
    "The remote collective is being scanned for peer DMDs.",
)

_DMDS = Field(
    "dmds",
    lambda participants: list(participant.name for participant in participants),
    "The (D)istributed (M)utable (D)irectories belonging to each participant are being scanned for changes.",
)

COLLECTIVE_SCAN = MessageType(
    "magic-folder:downloader:get-latest-file:collective-scan",
    [_DMDS],
    "Participants in the collective are being scanned.",
)


SCAN_REMOTE_DMD = ActionType(
    "magic-folder:scan-remote-dmd",
    [_NICKNAME],
    [],
    "A peer DMD is being scanned for changes.",
)

REMOTE_VERSION = Field.for_types(
    "remote_version",
    [int, long],
    "The version of a path found in a peer DMD.",
)

REMOTE_URI = Field.for_types(
    "remote_uri",
    [bytes],
    "The filecap of a path found in a peer DMD.",
)

ADD_TO_DOWNLOAD_QUEUE = MessageType(
    "magic-folder:add-to-download-queue",
    [RELPATH],
    "An entry was found to be changed and is being queued for download.",
)

MAGIC_FOLDER_STOP = ActionType(
    "magic-folder:stop",
    [_NICKNAME],
    [],
    "A Magic-Folder is being stopped.",
)

MAYBE_UPLOAD = MessageType(
    "magic-folder:maybe-upload",
    [RELPATH],
    "A decision is being made about whether to upload a file.",
)

PENDING = Field(
    "pending",
    lambda s: list(s),
    "The paths which are pending processing.",
    validateInstanceOf(set),
)

REMOVE_FROM_PENDING = ActionType(
    "magic-folder:remove-from-pending",
    [RELPATH, PENDING],
    [],
    "An item being processed is being removed from the pending set.",
)

PATH = Field(
    "path",
    lambda fp: fp.asTextMode().path,
    "A local filesystem path.",
    validateInstanceOf(FilePath),
)

NOTIFIED_OBJECT_DISAPPEARED = MessageType(
    "magic-folder:notified-object-disappeared",
    [PATH],
    "A path which generated a notification was not found on the filesystem.  This is normal.",
)

PROPAGATE_DIRECTORY_DELETION = ActionType(
    "magic-folder:propagate-directory-deletion",
    [],
    [],
    "Children of a deleted directory are being queued for upload processing.",
)

NO_DATABASE_ENTRY = MessageType(
    "magic-folder:no-database-entry",
    [],
    "There is no local database entry for a particular relative path in the magic folder.",
)

NOT_UPLOADING = MessageType(
    "magic-folder:not-uploading",
    [],
    "An item being processed is not going to be uploaded.",
)

SYMLINK = MessageType(
    "magic-folder:symlink",
    [PATH],
    "An item being processed was a symlink and is being skipped",
)

CREATED_DIRECTORY = Field.for_types(
    "created_directory",
    [unicode],
    "The relative path of a newly created directory in a magic-folder.",
)

PROCESS_DIRECTORY = ActionType(
    "magic-folder:process-directory",
    [],
    [CREATED_DIRECTORY],
    "An item being processed was a directory.",
)

NOT_NEW_DIRECTORY = MessageType(
    "magic-folder:not-new-directory",
    [],
    "A directory item being processed was found to not be new.",
)

NOT_NEW_FILE = MessageType(
    "magic-folder:not-new-file",
    [],
    "A file item being processed was found to not be new (or changed).",
)

SPECIAL_FILE = MessageType(
    "magic-folder:special-file",
    [],
    "An item being processed was found to be of a special type which is not supported.",
)

_COUNTER_NAME = Field.for_types(
    "counter_name",
    # Should really only be unicode
    [unicode, bytes],
    "The name of a counter.",
)

_DELTA = Field.for_types(
    "delta",
    [int, long],
    "An amount of a specific change in a counter.",
)

_VALUE = Field.for_types(
    "value",
    [int, long],
    "The new value of a counter after a change.",
)

COUNT_CHANGED = MessageType(
    "magic-folder:count",
    [_COUNTER_NAME, _DELTA, _VALUE],
    "The value of a counter has changed.",
)

_IGNORED = Field.for_types(
    "ignored",
    [bool],
    "A file proposed for queueing for processing is instead being ignored by policy.",
)

_ALREADY_PENDING = Field.for_types(
    "already_pending",
    [bool],
    "A file proposed for queueing for processing is already in the queue.",
)

_SIZE = Field.for_types(
    "size",
    [int, long, type(None)],
    "The size of a file accepted into the processing queue.",
)

_ABSPATH = Field.for_types(
    "abspath",
    [unicode],
    "The absolute path of a file being written in a local directory.",
)

_IS_CONFLICT = Field.for_types(
    "is_conflict",
    [bool],
    "An indication of whether a file being written in a local directory is in a conflicted state.",
)

_NOW = Field.for_types(
    "now",
    [int, long, float],
    "The time at which a file is being written in a local directory.",
)

_MTIME = Field.for_types(
    "mtime",
    [int, long, float, type(None)],
    "A modification time to put into the metadata of a file being written in a local directory.",
)

WRITE_DOWNLOADED_FILE = ActionType(
    "magic-folder:write-downloaded-file",
    [_ABSPATH, _SIZE, _IS_CONFLICT, _NOW, _MTIME],
    [],
    "A downloaded file is being written to the filesystem.",
)

ALREADY_GONE = MessageType(
    "magic-folder:rename:already-gone",
    [],
    "A deleted file could not be rewritten to a backup path because it no longer exists.",
)

_REASON = Field(
    "reason",
    lambda e: str(e),
    "An exception which may describe the form of the conflict.",
    validateInstanceOf(Exception),
)

OVERWRITE_BECOMES_CONFLICT = MessageType(
    "magic-folder:overwrite-becomes-conflict",
    [_REASON],
    "An attempt to overwrite an existing file failed because that file is now conflicted.",
)

_FILES = Field(
    "files",
    lambda file_set: list(file_set),
    "All of the relative paths belonging to a Magic-Folder that are locally known.",
)

ALL_FILES = MessageType(
    "magic-folder:all-files",
    [_FILES],
    "A record of the rough state of the local database at the time of downloader start up.",
)

_ITEMS = Field(
    "items",
    lambda deque: list(dict(relpath=item.relpath_u, kind=item.kind) for item in deque),
    "Items in a processing queue.",
)

ITEM_QUEUE = MessageType(
    "magic-folder:item-queue",
    [_ITEMS],
    "A report of the items in the processing queue at this point.",
)

_BATCH = Field(
    "batch",
    # Just report the paths for now.  Perhaps something from the values would
    # also be useful, though?  Consider it.
    lambda batch: batch.keys(),
    "A batch of scanned items.",
    validateInstanceOf(dict),
)

SCAN_BATCH = MessageType(
    "magic-folder:scan-batch",
    [_BATCH],
    "Items in a batch of files which were scanned from the DMD.",
)

START_DOWNLOADING = ActionType(
    "magic-folder:start-downloading",
    [_NICKNAME, _DIRECTION],
    [],
    "A Magic-Folder downloader is initializing and beginning to manage downloads.",
)

PERFORM_SCAN = ActionType(
    "magic-folder:perform-scan",
    [],
    [],
    "Remote storage is being scanned for changes which need to be synchronized.",
)

_CONFLICT_REASON = Field.for_types(
    "conflict_reason",
    [unicode, type(None)],
    "A human-readable explanation of why a file was in conflict.",
    validateSetMembership(
        {
            "dbentry mismatch metadata",
            "dbentry newer version",
            "last_downloaded_uri mismatch",
            "file appeared",
            None,
        }
    ),
)

CHECKING_CONFLICTS = ActionType(
    "magic-folder:item:checking-conflicts",
    [],
    [_IS_CONFLICT, _CONFLICT_REASON],
    "A potential download item is being checked to determine if it is in a conflicted state.",
)

REMOTE_DIRECTORY_CREATED = MessageType(
    "magic-folder:remote-directory-created",
    [],
    "The downloader found a new directory in the DMD.",
)

REMOTE_DIRECTORY_DELETED = MessageType(
    "magic-folder:remote-directory-deleted",
    [],
    "The downloader found a directory has been deleted from the DMD.",
)
