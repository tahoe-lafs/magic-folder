"""
Classes and services relating to the operation of the Downloader
"""

import os
from collections import deque
import hashlib

import attr
from attr.validators import (
    instance_of,
)

from zope.interface import (
    Interface,
    implementer,
)

from eliot.twisted import (
    inline_callbacks,
)
from eliot import (
    log_call,
    Message,
    start_action,
    write_traceback,
)

from twisted.application import (
    service,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    DeferredLock,
    maybeDeferred,
    returnValue,
    gatherResults,
    succeed,
)
from twisted.internet.interfaces import (
    IReactorTime,
)

from .snapshot import (
    create_snapshot_from_capability,
)
from .status import (
    IStatus,
    PollerStatus,
)
from .util.file import (
    PathState,
    get_pathinfo,
    seconds_to_ns,
)
from .util.twisted import (
    exclusively,
    PeriodicService,
)
from .util.capabilities import (
    Capability,
)


@attr.s
@implementer(service.IService)
class RemoteSnapshotCacheService(service.Service):
    """
    When told about Snapshot capabilities we download them.

    Our work queue is ephemeral; it is not synchronized to disk and
    will vanish if we are re-started. When we download a Remote
    Snapshot we *do not* download the content, just the Snapshot
    itself.

    Being ephemeral is okay because we will retry the operation the
    next time we restart. That is, we only need the RemoteSnapshot
    cached until we decide what to do and synchronize local state.

    Anyway, we do need to download all parent snapshots.

    Note: we *could* keep all this in our database .. but then we have
    to evict things from it at some point.
    """
    folder_config = attr.ib()
    tahoe_client = attr.ib()
    # We maintain the invariant that either these snapshots are closed
    # under taking parents, or we are locked, and the remaining parents
    # are queued to be downloaded
    # maps capability-string -> RemoteSnapshot
    _cached_snapshots = attr.ib(factory=dict)
    _lock = attr.ib(init=False, factory=DeferredLock)

    @classmethod
    def from_config(cls, config, tahoe_client):
        """
        Create a RemoteSnapshotCacheService from the MagicFolder
        configuration.
        """
        return cls(config, tahoe_client)

    @exclusively
    @inline_callbacks
    def get_snapshot_from_capability(self, snapshot_cap):
        """
        Download the given immutable Snapshot capability and all its parents.

        (When we have signatures this should verify the signature
        before downloading anything else)

        :param Capability snapshot_cap: an immutable directory capability-string

        :returns Deferred[RemoteSnapshot]: a Deferred that fires with
            the RemoteSnapshot when this item has been processed (or
            errbacks if any of the downloads fail).
        """
        if not isinstance(snapshot_cap, Capability):
            raise TypeError("not a cap")
        with start_action(action_type="cache-service:locate_snapshot") as t:
            try:
                snapshot = self._cached_snapshots[snapshot_cap.danger_real_capability_string()]
                t.add_success_fields(cached=True)
            except KeyError:
                t.add_success_fields(cached=False)
                snapshot = yield self._cache_snapshot(snapshot_cap)
        returnValue(snapshot)

    @inline_callbacks
    def _cache_snapshot(self, snapshot_cap):
        """
        Internal helper.

        :param Capability snapshot_cap: capability-string of a Snapshot

        Cache a single snapshot, which we shall return. We also cache
        all parent snapshots.
        """
        snapshot = yield create_snapshot_from_capability(
            snapshot_cap,
            self.tahoe_client,
        )
        self._cached_snapshots[snapshot_cap.danger_real_capability_string()] = snapshot
        Message.log(
            message_type="remote-cache:cached",
            relpath=snapshot.metadata["relpath"],
        )

        # breadth-first traversal of the parents
        q = deque([snapshot])
        while q:
            snap = q.popleft()
            for parent_cap in snap.parents_raw:
                if parent_cap in self._cached_snapshots:
                    # either a previous call cached this snapshot
                    # or we've already queued it for traversal
                    continue
                parent = yield create_snapshot_from_capability(
                    Capability.from_string(parent_cap),
                    self.tahoe_client,
                )
                self._cached_snapshots[parent.capability.danger_real_capability_string()] = parent
                q.append(parent)

        returnValue(snapshot)

    def is_ancestor_of(self, target_cap, child_cap):
        """
        Check if 'target_cap' is an ancestor of 'child_cap'

        .. note::

            ``child_cap`` must have already have been dowloaded

        :returns bool:
        """
        # TODO: We can make this more efficent in the future by tracking some extra data.
        # - for each snapshot in our remotesnapshotdb, we are going to check if something is
        #   an ancestor very often, so could cache that information (we'd probably want to
        #   integrate this with whatever updates that, as moving our remotesnapshot forward
        #   only incrementally adds to the ancestors of the remote
        # - for checking in the other direction, we can skip checking parents of any ancestors that are
        #   also ancestors of our remotesnapshot
        assert child_cap.danger_real_capability_string() in self._cached_snapshots is not None, "Remote should be cached already"
        snapshot = self._cached_snapshots[child_cap.danger_real_capability_string()]

        q = deque([snapshot])
        while q:
            snap = q.popleft()
            for parent_cap in snap.parents_raw:
                if target_cap.danger_real_capability_string() == parent_cap:
                    return True
                else:
                    q.append(self._cached_snapshots[parent_cap])
        return False


class IMagicFolderFilesystem(Interface):
    """
    An object that can make changes to the local filesystem
    magic-directory. It has a staging area to put files into so that
    there are no 'partial' files in the magic-folder.
    """

    def download_content_to_staging(relpath, file_cap, tahoe_client):
        """
        Prepare the content by downloading it.

        :returns Deferred[FilePath]: the location of the downloaded
            content (or errback if the download fails).
        """

    def mark_overwrite(relpath, mtime, staged_content, prior_pathstate):
        """
        This snapshot is an overwrite. Move it from the staging area over
        top of the existing file (if any) in the magic-folder.

        :param FilePath staged_content: a local path to the downloaded
            content.

        :return PathState: The path state of the file that was written.
        """

    def mark_conflict(relpath, conflict_path, staged_content):
        """
        This snapshot causes a conflict. The existing magic-folder file is
        untouched. The downloaded / prepared content shall be moved to
        a file named `<path>.theirs.<name>` where `<name>` is the
        petname of the author of the conflicting snapshot and `<path>`
        is the relative path inside the magic-folder.

        XXX can deletes conflict? if so staged_content would be None

        :param conflicting_snapshot: the RemoteSnapshot that conflicts

        :param FilePath staged_content: a local path to the downloaded
            content.
        """

    def mark_delete(relpath):
        """
        Mark this snapshot as a delete. The existing magic-folder file
        shall be deleted.
        """


@attr.s(auto_exc=True)
class BackupRetainedError(Exception):
    path = attr.ib(instance_of(FilePath))

    def __str__(self):
        return "Local backup {} has unexpected modification-time; not deleting!".format(
            self.path.path,
        )



@implementer(IMagicFolderFilesystem)
@attr.s
class LocalMagicFolderFilesystem(object):
    """
    Makes changes to a local directory.
    """

    magic_path = attr.ib(validator=instance_of(FilePath))
    staging_path = attr.ib(validator=instance_of(FilePath))

    @inline_callbacks
    def download_content_to_staging(self, relpath, file_cap, tahoe_client):
        """
        IMagicFolderFilesystem API
        """
        assert file_cap is not None, "must supply a file-cap"
        relpath_hasher = hashlib.sha256()  # rather blake2b, but is it all-platform?
        relpath_hasher.update(file_cap.danger_real_capability_string().encode("ascii"))
        relpath_hasher.update(relpath.encode("utf8"))
        staged_path = self.staging_path.child(relpath_hasher.hexdigest())
        with staged_path.open('wb') as f:
            yield tahoe_client.stream_capability(file_cap, f)
        returnValue(staged_path)

    @log_call(action_type="downloader:filesystem:mark-overwrite", include_args=[], include_result=False)
    def mark_overwrite(self, relpath, mtime, staged_content, prior_pathstate):
        """
        This snapshot is an overwrite. Move it from the staging area over
        top of the existing file (if any) in the magic-folder.

        :param FilePath staged_content: a local path to the downloaded
            content.

        :return PathState: The path state of the file that was written.
        """
        # Could be items associated with the action but then we have to define
        # an action type to teach Eliot how to serialize the remote snapshot
        # and the FilePath.  Probably *should* do that but just doing this for
        # now...  Maybe it should be /easier/ to do that?
        Message.log(
            message_type=u"downloader:filesystem:mark-overwrite",
            content_relpath=relpath,
            staged_content_path=staged_content.path,
        )
        local_path = self.magic_path.preauthChild(relpath)
        tmp = None
        if local_path.exists():
            # As long as the poller is running in-process, in the reactor,
            # it won't see this temporary file.
            # https://github.com/LeastAuthority/magic-folder/pull/451#discussion_r660885345

            tmp = local_path.temporarySibling(".snaptmp")
            local_path.moveTo(tmp)
            Message.log(
                message_type=u"downloader:filesystem:mark-overwrite:set-aside-existing",
                source_path=local_path.path,
                target_path=tmp.path,
            )
        os.utime(staged_content.path, (mtime, mtime))

        # We want to get the path state of the file we are about to write.
        # Ideally, we would get this from the staged file. However, depending
        # on the operating system, the ctime can change when we rename.
        # Instead, we get the path state before and after the move, and use
        # the latter if the mtime and size match.
        staged_path_state = get_pathinfo(staged_content).state
        with start_action(
            action_type=u"downloader:filesystem:mark-overwrite:emplace",
            content_final_path=local_path.path,
        ):
            if not local_path.parent().exists():
                local_path.parent().makedirs()
            else:
                if not local_path.parent().isdir():
                    # XXX this probably needs a better answer but for
                    # now at least we can signal a problem.
                    raise RuntimeError(
                        "Wanted to write a subdir of {} but it's not a directory".format(
                            local_path.parent().path,
                        )
                    )
            staged_content.moveTo(local_path)
        path_state = get_pathinfo(local_path).state
        if (
            staged_path_state.mtime_ns != path_state.mtime_ns
            or staged_path_state.size != path_state.size
            or staged_path_state.ctime_ns > path_state.ctime_ns
        ):
            # The path got changed after we moved it, return the staged
            # path_state so it is detected as a change.
            path_state = staged_path_state

        if tmp is not None:
            with start_action(
                action_type=u"downloader:filesystem:mark-overwrite:dispose-existing",
            ):
                # prior_pathstate comes from inside the state-machine
                # _immediately_ before we conclude "there's no
                # download conflict" (see _check_local_update in
                # Magicfile) .. here, we double-check that the
                # modification time and size match what we checked
                # against in _check_local_update.

                # if prior_pathstate is None, there was no file at all
                # before this function ran .. so we must preserve the
                # tmpfile
                #
                # otherwise, the pathstate of the tmp file must match
                # what was there before

                tmp.restat()
                modtime = seconds_to_ns(tmp.getModificationTime())
                size = tmp.getsize()

                if prior_pathstate is None or \
                   prior_pathstate.mtime_ns != modtime or \
                   prior_pathstate.size != size:
                    # "something" has written to the file (or the
                    # tmpfile, after it was moved) causing mtime or
                    # size to be differt; OR prior_pathstate points at
                    # a directory (or some other reason causing
                    # get_fileinfo() to return a None pathstate
                    # above). Thus, we keep the file as a backup

                    # the MagicFile statemachine knows about this
                    # exception and processes it separately -- turning
                    # it into a conflict
                    raise BackupRetainedError(
                        tmp,
                    )
                else:
                    # all good, the times + size match
                    tmp.remove()

        return path_state

    def mark_conflict(self, relpath, conflict_path, staged_content):
        """
        This snapshot causes a conflict. The existing magic-folder file is
        untouched. The downloaded / prepared content shall be moved to
        a file named `<path>.theirs.<name>` where `<name>` is the
        petname of the author of the conflicting snapshot and `<path>`
        is the relative path inside the magic-folder.

        XXX can deletes conflict? if so staged_content would be None

        :param conflicting_snapshot: the RemoteSnapshot that conflicts

        :param FilePath staged_content: a local path to the downloaded
            content.
        """
        local_path = self.magic_path.preauthChild(conflict_path)
        staged_content.moveTo(local_path)

    def mark_delete(self, relpath):
        """
        Mark this snapshot as a delete. The existing magic-folder file
        shall be deleted.
        """
        local_path = self.magic_path.preauthChild(relpath)
        local_path.remove()


@implementer(IMagicFolderFilesystem)
class InMemoryMagicFolderFilesystem(object):
    """
    Simply remembers the changes that would be made to a local
    filesystem. Generally for testing.
    """

    def __init__(self):
        self.actions = []
        self._staged_content = {}

    def download_content_to_staging(self, relpath, file_cap, tahoe_client):
        self.actions.append(
            ("download", relpath, file_cap)
        )
        marker = object()
        self._staged_content[marker] = file_cap
        return succeed(marker)

    def mark_overwrite(self, relpath, mtime, staged_content, prior_pathstate):
        assert staged_content in self._staged_content, "Overwrite but no staged content"
        self.actions.append(
            ("overwrite", relpath, self._staged_content[staged_content])
        )
        mtime_ns = seconds_to_ns(mtime)
        return PathState(
            mtime_ns=mtime_ns,
            ctime_ns=mtime_ns,
            size=0,
        )

    def mark_conflict(self, relpath, conflict_path, staged_content):
        assert staged_content in self._staged_content
        self.actions.append(
            ("conflict", relpath, conflict_path, self._staged_content[staged_content])
        )

    def mark_delete(self, relpath):
        self.actions.append(
            ("delete", relpath)
        )


@attr.s
@implementer(service.IService)
class RemoteScannerService(service.MultiService):
    """
    A service that periodically polls the Colletive DMD for new
    RemoteSnapshot capabilities to download.
    """

    _clock = attr.ib(validator=attr.validators.provides(IReactorTime))
    _config = attr.ib()
    _participants = attr.ib()
    _file_factory = attr.ib()  # MagicFileFactory instance
    _remote_snapshot_cache = attr.ib(validator=instance_of(RemoteSnapshotCacheService))
    _status = attr.ib(validator=attr.validators.provides(IStatus))

    @classmethod
    def from_config(cls, clock, name, config, participants, file_factory, remote_snapshot_cache, status_service):
        """
        Create a RemoteScannerService from the MagicFolder configuration.
        """
        return cls(
            clock,
            config,
            participants,
            file_factory,
            remote_snapshot_cache,
            status_service,
        )

    def __attrs_post_init__(self):
        service.MultiService.__init__(self)
        self._poller = PeriodicService(
            self._clock,
            self._config.poll_interval,
            self._loop,
        )
        self._poller.setServiceParent(self)

    @inline_callbacks
    def _loop(self):
        try:
            yield self._poll_collective()
            self._status.poll_status(
                self._config.name,
                PollerStatus(
                    last_completed=seconds_to_ns(self._clock.seconds()),
                )
            )
        except Exception:
            # in some cases, might want to surface elsewhere
            write_traceback()

    @inline_callbacks
    def poll_once(self):
        """
        Perform a remote poll.

        :returns Deferred[None]: fires when the poll is completed
        """
        yield self._poller.call_soon()

    def _is_remote_update(self, relpath, snapshot_cap):
        """
        Predicate to determine if the given snapshot_cap for a particular
        relpath is an update or not
        """
        # if this remote matches what we believe to be the latest then
        # it is _not_ an update. otherwise, it is.
        try:
            our_snapshot_cap = self._config.get_remotesnapshot(relpath)
            return our_snapshot_cap != snapshot_cap
        except KeyError:
            return True

    @inline_callbacks
    def _poll_collective(self):
        """
        Internal helper.
        Download the collective dircap.
        For every participant that's not us, download any updated entries.
        """
        with start_action(
            action_type="downloader:poll-collective", folder=self._config.name
        ):
            # XXX any errors here should (probably) be reported to the
            # status service ..
            participants = yield self._participants.list()
            updates = []  # 2-tuples (relpath, snapshot-cap)
            for participant in participants:
                with start_action(
                    action_type="downloader:poll-participant",
                    participant=participant.name,
                    is_self=participant.is_self,
                ):
                    if participant.is_self:
                        # we don't download from ourselves
                        continue
                    files = yield participant.files()
                    for relpath, file_data in files.items():
                        if self._is_remote_update(relpath, file_data.snapshot_cap):
                            self._status.download_queued(self._config.name, relpath)
                            updates.append((relpath, file_data.snapshot_cap))

            # allow for parallel downloads
            yield gatherResults([
                self._process_snapshot(relpath, snapshot_cap)
                for relpath, snapshot_cap in updates
            ])

    @inline_callbacks
    def _process_snapshot(self, relpath, snapshot_cap):
        """
        Internal helper.

        Does the work of caching and acting on a single remote
        Snapshot.

        :returns Deferred: fires with None upon completion
        """
        with start_action(
            action_type="downloader:poll-file",
            relpath=relpath,
        ):
            snapshot = yield self._remote_snapshot_cache.get_snapshot_from_capability(snapshot_cap)
            try:
                our_snapshot_cap = self._config.get_remotesnapshot(relpath)
            except KeyError:
                our_snapshot_cap = None

            # this method is only called if snapshot.capability != our_snapshot_cap
            assert snapshot.capability != our_snapshot_cap, "invalid input"

            # make sure "our_snapshot_cap" is cached
            if our_snapshot_cap is not None:
                yield self._remote_snapshot_cache.get_snapshot_from_capability(our_snapshot_cap)
            abspath = self._config.magic_path.preauthChild(snapshot.relpath)
            mf = self._file_factory.magic_file_for(abspath)
            yield maybeDeferred(mf.found_new_remote, snapshot)
