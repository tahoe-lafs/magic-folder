"""
Classes and services relating to the operation of the Downloader
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import os
from collections import deque

import attr
from attr.validators import (
    provides,
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
    write_failure,
)

from twisted.application import (
    service,
    internet,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    DeferredLock,
    returnValue,
    succeed,
)
from twisted.internet.interfaces import (
    IReactorTime,
)

from .config import (
    MagicFolderConfig,
)
from .participants import IWriteableParticipant
from .snapshot import (
    create_snapshot_from_capability,
)
from .util.file import (
    PathState,
    get_pathinfo,
    seconds_to_ns,
)
from .util.twisted import (
    exclusively,
)
from .status import (
    FolderStatus,
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

    Anyway, we do need to download parent snapshots UNTIL we reach the
    current remotesnapshot that we've noted for that name (or run
    out of parents).

    Note: we *could* keep all this in our database .. but then we have
    to evict things from it at some point.
    """
    folder_config = attr.ib()
    tahoe_client = attr.ib()
    # We maintain the invariant that either these snapshots are closed
    # under taking parents, or we are locked, and the remaining parents
    # are queued to be downloaded
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

        :param bytes snapshot_cap: an immutable directory capability-string

        :returns Deferred[RemoteSnapshot]: a Deferred that fires with
            the RemoteSnapshot when this item has been processed (or
            errbacks if any of the downloads fail).
        """
        with start_action(action_type="cache-service:locate_snapshot",
                          capability=snapshot_cap) as t:
            try:
                snapshot = self._cached_snapshots[snapshot_cap]
                t.add_success_fields(cached=True)
            except KeyError:
                t.add_success_fields(cached=False)
                snapshot = yield self._cache_snapshot(snapshot_cap)
        returnValue(snapshot)

    @inline_callbacks
    def _cache_snapshot(self, snapshot_cap):
        """
        Internal helper.

        :param bytes snapshot_cap: capability-string of a Snapshot

        Cache a single snapshot, which we shall return. We also cache
        parent snapshots until we find 'ours' -- that is, whatever our
        config's remotesnapshot table points at for this name. (If we
        don't have an entry for it yet, we cache all parents .. which
        will also be the case when we don't find 'our' snapshot at
        all).
        """
        snapshot = yield create_snapshot_from_capability(
            snapshot_cap,
            self.tahoe_client,
        )
        self._cached_snapshots[snapshot_cap] = snapshot
        Message.log(message_type="remote-cache:cached",
                    name=snapshot.name,
                    capability=snapshot.capability)

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
                    parent_cap,
                    self.tahoe_client,
                )
                self._cached_snapshots[parent.capability] = parent
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
        assert child_cap in self._cached_snapshots is not None, "Remote should be cached already"
        snapshot = self._cached_snapshots[child_cap]

        q = deque([snapshot])
        while q:
            snap = q.popleft()
            for parent_cap in snap.parents_raw:
                if target_cap == parent_cap:
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

    def mark_overwrite(relpath, mtime, staged_content):
        """
        This snapshot is an overwrite. Move it from the staging area over
        top of the existing file (if any) in the magic-folder.

        :param FilePath staged_content: a local path to the downloaded
            content.

        :return PathState: The path state of the file that was written.
        """

    def mark_conflict(relpath, remote_snapshot, staged_content):
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

    def mark_delete(relpath, remote_snapshot):
        """
        Mark this snapshot as a delete. The existing magic-folder file
        shall be deleted.
        """




@attr.s
class MagicFolderUpdater(object):
    """
    Updates the local magic-folder when given locally-cached
    RemoteSnapshots. These RemoteSnapshot instance must have all
    relevant parents available (via the cache service).
    FIXME: we aren't guaranteed this
    -> why not?

    "Relevant" here means all parents unless we find a common
    ancestor.
    """
    _clock = attr.ib(validator=provides(IReactorTime))
    _magic_fs = attr.ib(validator=provides(IMagicFolderFilesystem))
    _config = attr.ib(validator=instance_of(MagicFolderConfig))
    _remote_cache = attr.ib(validator=instance_of(RemoteSnapshotCacheService))
    tahoe_client = attr.ib() # validator=instance_of(TahoeClient))
    _status = attr.ib(validator=attr.validators.instance_of(FolderStatus))
    _write_participant = attr.ib(validator=provides(IWriteableParticipant))
    _lock = attr.ib(init=False, factory=DeferredLock)

    @exclusively
    @inline_callbacks
    def add_remote_snapshot(self, relpath, snapshot):
        """
        :returns Deferred: fires with None when this RemoteSnapshot has
            been processed (or errback if that fails).
        """
        # the lock ensures we only run _process() one at a time per
        # process
        with start_action(action_type="downloader:modify_filesystem"):
            yield self._process(relpath, snapshot)

    @inline_callbacks
    def _process(self, relpath, snapshot):
        """
        Internal helper.

        Determine the disposition of 'snapshot' and propose
        appropriate filesystem modifications. Once these are done,
        note the new snapshot-cap in our database and then push it to
        Tahoe (i.e. to our Personal DMD)
        """
        conflict_path = relpath + ".conflict-{}".format(snapshot.author.name)

        with start_action(action_type="downloader:updater:process",
                          name=snapshot.name,
                          capability=snapshot.capability,
                          ) as action:
            local_path = self._config.magic_path.preauthChild(relpath)

            # see if we have existing local or remote snapshots for
            # this name already
            try:
                remote_cap = self._config.get_remotesnapshot(relpath)
                # w/ no KeyError we have seen this before
                action.add_success_fields(remote=remote_cap)
            except KeyError:
                remote_cap = None

            try:
                local_snap = self._config.get_local_snapshot(relpath)
                action.add_success_fields(
                    local=local_snap.content_path.path,
                )
            except KeyError:
                local_snap = None

            # note: if local_snap and remote_cap are both non-None
            # then remote_cap should already be the ancestor of
            # local_snap by definition.

            # XXX check if we're already conflicted; if so, do not continue

            # XXX not dealing with deletes yet

            is_conflict = False
            local_timestamp = None
            if local_path.exists():
                local_timestamp = local_path.getModificationTime()
                # there is a file here already .. if we have any
                # LocalSnapshots for this name, then we've got local
                # edits and it must be a conflict. If we have nothing
                # in our remotesnapshot database then we've just not
                # made a LocalSnapshot yet (so it's still a conflict).
                if local_snap is not None or remote_cap is None:
                    is_conflict = True
                    action.add_success_fields(conflict_reason="existing-file-no-remote")

                else:
                    assert remote_cap != snapshot.capability, "already match"

                    # "If the new snapshot is a descendant of the client's
                    # existing snapshot, then this update is an 'overwrite'"

                    ancestor = self._remote_cache.is_ancestor_of(remote_cap, snapshot.capability)
                    action.add_success_fields(ancestor=ancestor)

                    if not ancestor:
                        # if the incoming remotesnapshot is actually an
                        # ancestor of _our_ snapshot, then we have nothing
                        # to do because we are newer
                        if self._remote_cache.is_ancestor_of(snapshot.capability, remote_cap):
                            return
                        action.add_success_fields(conflict_reason="no-ancestor")
                        is_conflict = True

            else:
                # there is no local file
                # XXX we don't handle deletes yet
                assert not local_snap and not remote_cap, "Internal inconsistency: record of a Snapshot for this name but no local file"
                is_conflict = False

            action.add_success_fields(is_conflict=is_conflict)
            self._status.download_started(relpath)
            try:
                with start_action(
                    action_type=u"downloader:updater:content-to-staging",
                    name=snapshot.name,
                    capability=snapshot.capability,
                ):
                    try:
                        staged = yield self._magic_fs.download_content_to_staging(relpath, snapshot.content_cap, self.tahoe_client)
                    except Exception:
                        self._status.error_occurred(
                            "Failed to download snapshot for '{}'.".format(relpath)
                        )
                        raise

            finally:
                self._status.download_finished(relpath)

            # once here, we know if we have a conflict or not. if
            # there is nothing to do, we shold have returned
            # already. so mark an overwrite or conflict into the
            # filesystem.
            if is_conflict:
                self._magic_fs.mark_conflict(relpath, conflict_path, staged)
                # FIXME probably want to also record internally that
                # this is a conflict.

            else:
                # there is a longer dance described in detail in
                # https://magic-folder.readthedocs.io/en/latest/proposed/magic-folder/remote-to-local-sync.html#earth-dragons-collisions-between-local-filesystem-operations-and-downloads
                # which may do even better at reducing the window for
                # local changes to get overwritten. Currently, that
                # window is the 3 python statements between here and
                # ".mark_overwrite()"
                last_minute_change = False
                if local_timestamp is None:
                    if local_path.exists():
                        last_minute_change = True
                else:
                    local_path.changed()
                    if local_path.getModificationTime() != local_timestamp:
                        last_minute_change = True
                if last_minute_change:
                    action.add_success_fields(conflict_reason="last-minute-change")
                    self._magic_fs.mark_conflict(relpath, conflict_path, staged)
                    # FIXME note conflict internally
                else:
                    try:
                        path_state = self._magic_fs.mark_overwrite(relpath, snapshot.metadata["modification_time"], staged)
                    except OSError as e:
                        self._status.error_occurred(
                            "Failed to overwrite file '{}': {}".format(relpath, str(e))
                        )
                        raise

                    # Note, if we crash here (after moving the file into place
                    # but before noting that in our database) then we could
                    # produce LocalSnapshots referencing the wrong parent. We
                    # will no longer produce snapshots with the wrong parent
                    # once we re-run and get past this point.

                    # remember the last remote we've downloaded
                    self._config.store_downloaded_snapshot(
                        relpath, snapshot, path_state
                    )

                    # XXX careful here, we still need something that makes
                    # sure mismatches between remotesnapshots in our db and
                    # the Personal DMD are reconciled .. that is, if we crash
                    # here and/or can't update our Personal DMD we need to
                    # retry later.
                    yield self._write_participant.update_snapshot(
                        relpath,
                        snapshot.capability.encode("ascii"),
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
        import hashlib
        h = hashlib.sha256()
        h.update(file_cap)
        staged_path = self.staging_path.child(h.hexdigest())
        with staged_path.open('wb') as f:
            yield tahoe_client.stream_capability(file_cap, f)
        returnValue(staged_path)

    @log_call(action_type="downloader:filesystem:mark-overwrite", include_args=[], include_result=False)
    def mark_overwrite(self, relpath, mtime, staged_content):
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
            # As long as the scanner is running in-process, in the reactor,
            # it won't see this temporary file.
            # https://github.com/LeastAuthority/magic-folder/pull/451#discussion_r660885345
            tmp = local_path.temporarySibling(b".snaptmp")
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
        # the later if the mtime and size match.
        staged_path_state = get_pathinfo(staged_content).state
        with start_action(
            action_type=u"downloader:filesystem:mark-overwrite:emplace",
            content_final_path=local_path.path,
        ):
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
                # FIXME: We should verify that this moved file has
                # the same (except ctime) state as the the previous snapshot
                # before we remove it.
                # https://github.com/LeastAuthority/magic-folder/issues/454
                # https://github.com/LeastAuthority/magic-folder/issues/454#issuecomment-870923942
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

    def mark_delete(remote_snapshot):
        """
        Mark this snapshot as a delete. The existing magic-folder file
        shall be deleted.
        """
        raise NotImplementedError()


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

    def mark_overwrite(self, relpath, mtime, staged_content):
        assert staged_content in self._staged_content
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

    def mark_delete(self, relpath, remote_snapshot):
        self.actions.append(
            ("delete", relpath, remote_snapshot)
        )


@attr.s
@implementer(service.IService)
class DownloaderService(service.MultiService):
    """
    A service that periodically polls the Colletive DMD for new
    RemoteSnapshot capabilities to download.
    """

    _config = attr.ib()
    _participants = attr.ib()
    _status = attr.ib(validator=attr.validators.instance_of(FolderStatus))
    _remote_snapshot_cache = attr.ib(validator=instance_of(RemoteSnapshotCacheService))
    _folder_updater = attr.ib(validator=instance_of(MagicFolderUpdater))
    _tahoe_client = attr.ib()

    @classmethod
    def from_config(cls, name, config, participants, status, remote_snapshot_cache, folder_updater, tahoe_client):
        """
        Create a DownloaderService from the MagicFolder configuration.
        """
        return cls(
            config,
            participants,
            status,
            remote_snapshot_cache,
            folder_updater,
            tahoe_client,
        )

    def __attrs_post_init__(self):
        service.MultiService.__init__(self)
        self._scanner = internet.TimerService(
            self._config.poll_interval,
            self._loop,
        )
        self._scanner.setServiceParent(self)

    def _loop(self):
        d = self._scan_collective()
        # in some cases, might want to surface elsewhere
        d.addErrback(write_failure)

    @inline_callbacks
    def _scan_collective(self):
        with start_action(
            action_type="downloader:scan-collective", folder=self._config.name
        ):
            for participant in (yield self._participants.list()):
                with start_action(
                    action_type="downloader:scan-participant",
                    participant=participant.name,
                    is_self=participant.is_self,
                ):
                    if participant.is_self:
                        # we don't download from ourselves
                        continue
                    files = yield participant.files()
                    for relpath, file_data in files.items():
                        yield self._process_snapshot(relpath, file_data.snapshot_cap)

    @inline_callbacks
    def _process_snapshot(self, relpath, snapshot_cap):
        """
        Internal helper.

        Does the work of caching and acting on a single remote
        Snapshot.

        :returns Deferred: fires with None upon completion
        """
        with start_action(
            action_type="downloader:scan-file",
            relpath=relpath,
            remote_cap=snapshot_cap,
        ):
            snapshot = yield self._remote_snapshot_cache.get_snapshot_from_capability(snapshot_cap)
            # if this remote matches what we believe to be the
            # latest, there is nothing to do .. otherwise, we
            # have to figure out what to do
            try:
                our_snapshot_cap = self._config.get_remotesnapshot(relpath)
            except KeyError:
                our_snapshot_cap = None
            if snapshot.capability != our_snapshot_cap:
                if our_snapshot_cap is not None:
                    yield self._remote_snapshot_cache.get_snapshot_from_capability(our_snapshot_cap)
                yield self._folder_updater.add_remote_snapshot(relpath, snapshot)
