import os
import attr
import six
import sys
import os.path
from errno import EEXIST
import ConfigParser

from twisted.python.filepath import FilePath
from twisted.python.monkey import MonkeyPatcher
from twisted.internet import defer
from twisted.python import runtime
from twisted.application import service
from twisted.internet import task

from zope.interface import (
    Interface,
    implementer,
)
from twisted.internet.defer import (
    DeferredQueue,
    CancelledError,
)

from eliot import (
    Field,
    ActionType,
    MessageType,
    write_traceback,
)
from eliot.twisted import (
    inline_callbacks,
)

from allmydata.util import (
    fileutil,
    configutil,
    yamlutil,
)
from allmydata.uri import (
    from_string as tahoe_uri_from_string,
)
from .util.eliotutil import (
    RELPATH,
    validateSetMembership,
    validateInstanceOf,
    log_call_deferred,
)
from allmydata.util import log
from allmydata.util.encodingutil import to_filepath

from . import (
    magicpath,
)
from .snapshot import (
    write_snapshot_to_tahoe,
    create_snapshot,
    LocalAuthor,
)
from .participants import (
    participants_from_collective,
)
from .config import (
    MagicFolderConfig,
)

if six.PY3:
    long = int


# Mask off all non-owner permissions for magic-folders files by default.
_DEFAULT_DOWNLOAD_UMASK = 0o077

IN_EXCL_UNLINK = long(0x04000000)


class ConfigurationError(Exception):
    """
    There was something wrong with some magic-folder configuration.
    """


def _get_inotify_module():
    try:
        if sys.platform == "win32":
            from .windows import inotify
        elif runtime.platform.supportsINotify():
            from twisted.internet import inotify
        elif not sys.platform.startswith("linux"):
            from .watchdog import inotify
        else:
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "This currently requires Linux, Windows, or macOS.")
        return inotify
    except (ImportError, AttributeError) as e:
        log.msg(e)
        if sys.platform == "win32":
            raise NotImplementedError("filesystem notification needed for Magic Folder is not supported.\n"
                                      "Windows support requires at least Vista, and has only been tested on Windows 7.")
        raise


def get_inotify_module():
    # Until Twisted #9579 is fixed, the Docker check just screws things up.
    # Disable it.
    monkey = MonkeyPatcher()
    monkey.addPatch(runtime.platform, "isDocker", lambda: False)
    return monkey.runWithPatches(_get_inotify_module)


def is_new_file(pathinfo, db_entry):
    if db_entry is None:
        return True

    if not pathinfo.exists and db_entry.size is None:
        return False

    return ((pathinfo.size, pathinfo.ctime_ns, pathinfo.mtime_ns) !=
            (db_entry.size, db_entry.ctime_ns, db_entry.mtime_ns))


def load_magic_folders(node_directory):
    """
    Loads existing magic-folder configuration and returns it as a dict
    mapping name -> dict of config. This will NOT upgrade from
    old-style to new-style config (but WILL read old-style config and
    return in the same way as if it was new-style).

    :param node_directory: path where node data is stored
    :returns: dict mapping magic-folder-name to its config (also a dict)
    """
    yaml_fname = os.path.join(node_directory, u"private", u"magic_folders.yaml")
    folders = dict()

    config_fname = os.path.join(node_directory, u"tahoe.cfg")
    config = configutil.get_config(config_fname.encode("utf-8"))

    if not os.path.exists(yaml_fname):
        # there will still be a magic_folder section in a "new"
        # config, but it won't have local.directory nor poll_interval
        # in it.
        if config.has_option("magic_folder", "local.directory"):
            up_fname = os.path.join(node_directory, u"private", u"magic_folder_dircap")
            coll_fname = os.path.join(node_directory, u"private", u"collective_dircap")
            directory = config.get("magic_folder", "local.directory").decode('utf8')
            try:
                interval = int(config.get("magic_folder", "poll_interval"))
            except ConfigParser.NoOptionError:
                interval = 60

            if config.has_option("magic_folder", "download.umask"):
                umask = int(config.get("magic_folder", "download.umask"), 8)
            else:
                umask = _DEFAULT_DOWNLOAD_UMASK

            folders[u"default"] = {
                u"directory": directory,
                u"upload_dircap": fileutil.read(up_fname),
                u"collective_dircap": fileutil.read(coll_fname),
                u"poll_interval": interval,
                u"umask": umask,
            }
        else:
            # without any YAML file AND no local.directory option it's
            # an error if magic-folder is "enabled" because we don't
            # actually have enough config for any magic-folders at all
            if config.has_section("magic_folder") \
               and config.getboolean("magic_folder", "enabled") \
               and not folders:
                raise Exception(
                    "[magic_folder] is enabled but has no YAML file and no "
                    "'local.directory' option."
                )

    elif os.path.exists(yaml_fname):  # yaml config-file exists
        if config.has_option("magic_folder", "local.directory"):
            raise Exception(
                "magic-folder config has both old-style configuration"
                " and new-style configuration; please remove the "
                "'local.directory' key from tahoe.cfg or remove "
                "'magic_folders.yaml' from {}".format(node_directory)
            )
        with open(yaml_fname, "r") as f:
            magic_folders = yamlutil.safe_load(f.read())
            if not isinstance(magic_folders, dict):
                raise Exception(
                    "'{}' should contain a dict".format(yaml_fname)
                )

            folders = magic_folders['magic-folders']
            if not isinstance(folders, dict):
                raise Exception(
                    "'magic-folders' in '{}' should be a dict".format(yaml_fname)
                )

    # check configuration
    folders = dict(
        (name, fix_magic_folder_config(yaml_fname, name, config))
        for (name, config)
        in folders.items()
    )
    return folders


def fix_magic_folder_config(yaml_fname, name, config):
    """
    Check the given folder configuration for validity.

    If it refers to a local directory which does not exist, create that
    directory with the configured permissions.

    :param unicode yaml_fname: The configuration file from which the
        configuration was read.

    :param unicode name: The name of the magic-folder this particular
        configuration blob is associated with.

    :param config: The configuration for a single magic-folder.  This is
        expected to be a ``dict`` with certain keys and values of certain
        types but these properties will be checked.

    :raise ConfigurationError: If the given configuration object does not
        conform to some magic-folder configuration requirement.
    """
    if not isinstance(config, dict):
        raise ConfigurationError(
            "Each item in '{}' must itself be a dict".format(yaml_fname)
        )

    for k in ['collective_dircap', 'upload_dircap', 'directory', 'poll_interval']:
        if k not in config:
            raise ConfigurationError(
                "Config for magic folder '{}' is missing '{}'".format(
                    name, k
                )
            )

    if not isinstance(
        config.setdefault(u"umask", _DEFAULT_DOWNLOAD_UMASK),
        int,
    ):
        raise Exception("magic-folder download umask must be an integer")

    # make sure directory for magic folder exists
    dir_fp = to_filepath(config['directory'])
    umask = config.setdefault('umask', 0o077)

    try:
        os.mkdir(dir_fp.path, 0o777 & (~ umask))
    except OSError as e:
        if EEXIST != e.errno:
            # Report some unknown problem.
            raise ConfigurationError(
                "magic-folder {} configured path {} could not be created: "
                "{}".format(
                    name,
                    dir_fp.path,
                    str(e),
                ),
            )
        elif not dir_fp.isdir():
            # Tell the user there's a collision.
            raise ConfigurationError(
                "magic-folder {} configured path {} exists and is not a "
                "directory".format(
                    name, dir_fp.path,
                ),
            )

    result_config = config.copy()
    for k in ['collective_dircap', 'upload_dircap']:
        if isinstance(config[k], unicode):
            result_config[k] = config[k].encode('ascii')
    return result_config



def save_magic_folders(node_directory, folders):
    fileutil.write_atomically(
        os.path.join(node_directory, u"private", u"magic_folders.yaml"),
        yamlutil.safe_dump({u"magic-folders": folders}),
    )


class MagicFolder(service.MultiService):
    """
    :ivar LocalSnapshotService local_snapshot_service: A child service
        responsible for creating new local snapshots for files in this folder.
    """

    @classmethod
    def from_config(cls, reactor, tahoe_client, name, config):
        """
        Create a ``MagicFolder`` from a client node and magic-folder
        configuration.

        :param IReactorTime reactor: the reactor to use

        :param TahoeClient tahoe_client: Access the API of the
            Tahoe-LAFS client we're associated with.

        :param GlobalConfigurationDatabase config: our configuration
        """
        mf_config = config.get_magic_folder(name)

        from .cli import Node

        initial_participants = participants_from_collective(
            Node(tahoe_client, tahoe_uri_from_string(mf_config.collective_dircap)),
            Node(tahoe_client, tahoe_uri_from_string(mf_config.upload_dircap)),
        )

        return cls(
            client=tahoe_client,
            config=mf_config,
            name=name,
            local_snapshot_service=LocalSnapshotService(
                mf_config.magic_path,
                LocalSnapshotCreator(
                    mf_config,
                    mf_config.author,
                    mf_config.stash_path,
                ),
            ),
            uploader_service=UploaderService.from_config(
                clock=reactor,
                config=mf_config,
                remote_snapshot_creator=RemoteSnapshotCreator(
                    config=mf_config,
                    local_author=mf_config.author,
                    tahoe_client=tahoe_client,
                    upload_dircap=mf_config.upload_dircap,
                ),
            ),
            initial_participants=initial_participants,
            clock=reactor,
        )

    @property
    def name(self):
        # this is used by 'service' things and must be unique in this Service hierarchy
        return u"magic-folder-{}".format(self.folder_name)

    def __init__(self, client, config, name, local_snapshot_service, uploader_service, initial_participants, clock):
        super(MagicFolder, self).__init__()
        self.folder_name = name
        self._clock = clock
        self._config = config  # a MagicFolderConfig instance
        self._participants = initial_participants
        self.local_snapshot_service = local_snapshot_service
        self.uploader_service = uploader_service
        local_snapshot_service.setServiceParent(self)
        uploader_service.setServiceParent(self)

    def ready(self):
        """
        :returns: Deferred that fires with None when this magic-folder is
            ready to operate
        """
        return defer.succeed(None)


_NICKNAME = Field.for_types(
    u"nickname",
    [unicode, bytes],
    u"A Magic-Folder participant nickname.",
)

_DIRECTION = Field.for_types(
    u"direction",
    [unicode],
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
    [int, long],
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
    [int, long],
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
    lambda fp: fp.asTextMode().path,
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
    [unicode],
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
    [unicode, bytes],
    u"The name of a counter.",
)

_DELTA = Field.for_types(
    u"delta",
    [int, long],
    u"An amount of a specific change in a counter.",
)

_VALUE = Field.for_types(
    u"value",
    [int, long],
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
    [int, long, type(None)],
    u"The size of a file accepted into the processing queue.",
)

_ABSPATH = Field.for_types(
    u"abspath",
    [unicode],
    u"The absolute path of a file being written in a local directory.",
)

_IS_CONFLICT = Field.for_types(
    u"is_conflict",
    [bool],
    u"An indication of whether a file being written in a local directory is in a conflicted state.",
)

_NOW = Field.for_types(
    u"now",
    [int, long, float],
    u"The time at which a file is being written in a local directory.",
)

_MTIME = Field.for_types(
    u"mtime",
    [int, long, float, type(None)],
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
    [unicode, type(None)],
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
        d = defer.Deferred()
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
            self._upload_dircap,
            magicpath.path2magic(name),
            remote_snapshot.capability.encode('utf-8'),
            replace=True,
        )

        # Remove the local snapshot content from the stash area.
        snapshot.content_path.remove()

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
        self._processing_loop = task.LoopingCall(
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
