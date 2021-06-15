from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

"""
Classes and services relating to the operation of the Downloader
"""

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
    start_action,
    Message,
)

from twisted.application import (
    service,
    internet,
)
from twisted.python.filepath import (
    FilePath,
)
from twisted.python.failure import (
    Failure,
)
from twisted.internet.defer import (
    Deferred,
    DeferredQueue,
    inlineCallbacks,
    returnValue,
    CancelledError,
)
from twisted.web.error import (
    SchemeNotSupported,
)

from .config import (
    MagicFolderConfig,
)
from .magicpath import (
    magic2path,
)
from .snapshot import (
    create_snapshot_from_capability,
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

    XXX: the "remote-snapshots" database is kind of 'just a cache'
    too; we should be putting that information into our Personal DMD
    ... so what happens when it's out of date? (source-of-truth MUST
    be our Personal DMD ...)

     -> actually, maybe the local db should be the "source of truth":
    we only put entries into it if we're about to push it to Tahoe
    .. so if things don't match up, it's because we crashed before
    that happened (and so on startup, we should check and possibly
    push more things up to Tahoe)
    """
    folder_config = attr.ib()
    tahoe_client = attr.ib()
    cached_snapshots = attr.ib(default=attr.Factory(dict))
    _queue = attr.ib(default=attr.Factory(DeferredQueue))

    @classmethod
    def from_config(cls, config, tahoe_client):
        """
        Create a RemoteSnapshotCacheService from the MagicFolder
        configuration.
        """
        return cls(config, tahoe_client)

    def add_remote_capability(self, snapshot_cap):
        """
        Add the given immutable Snapshot capability to our queue.

        When this queue item is processed, we download the Snapshot,
        then download the metadata.

        (When we have signatures this should verify the signature
        before downloading anything else)

        We also download parents of this Snapshot until we find a
        common ancestor .. meaning we keep downloading parents until
        we find one that matches the remotesnapshot entry in our local
        database. If we have no entry for this Snapshot we download
        all parents (which will also be the case if there is no common
        ancestor).

        :param bytes snapshot_cap: an immutable directory capability-string

        :returns Deferred[RemoteSnapshot]: a Deferred that fires with
            the RemoteSnapshot when this item has been processed (or
            errbacks if any of the downloads fail).

        :raises QueueOverflow: if our queue is full
        """
        Message.log(
            message_type="cache-service:add-remote-capability",
            snapshot_cap=snapshot_cap,
        )
        d = Deferred()
        self._queue.put((snapshot_cap, d))
        return d

    def startService(self):
        """
        Pull new work from our queue, forever.
        """
        with start_action(message_type="cache-service:start"):
            service.Service.startService(self)
            self._service_d = self._process_queue()
            Message.log(starting=True)

    @inline_callbacks
    def _process_queue(self):
        """
        Wait for a single item from the queue and process it, forever.
        """
        while True:
            try:
                (snapshot_cap, d) = yield self._queue.get()
                with start_action(action_type="cache-service:locate_snapshot") as t:
                    try:
                        snapshot = self.cached_snapshots[snapshot_cap]
                        t.add_success_fields(cached=True)
                    except KeyError:
                        t.add_success_fields(cached=False)
                        snapshot = yield self._cache_snapshot(snapshot_cap)
                    d.callback(snapshot)
            except CancelledError:
                break
            except Exception:
                d.errback(Failure())

    @inlineCallbacks
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
        self.cached_snapshots[snapshot_cap] = snapshot

        # the target of our search through all parent Snapshots
        try:
            our_snapshot_cap = self.folder_config.get_remotesnapshot(snapshot.name)
        except KeyError:
            # we've never seen this one before
            our_snapshot_cap = None

        # breadth-first traversal of the parents; we can stop early if
        # we find the above capability (our current notion of the
        # current Snapshot for this name).
        q = deque([snapshot])
        while q:
            snap = q.popleft()
            if our_snapshot_cap in snap.parents_raw:
                break
            else:
                for i in range(len(snap.parents_raw)):
                    parent = yield snap.fetch_parent(self.tahoe_client, i)
                    self.cached_snapshots[parent.capability] = parent
                    q.append(parent)

        returnValue(snapshot)

    def stopService(self):
        """
        Don't process queued items anymore.
        """
        d = self._service_d
        self._service_d.cancel()
        service.Service.stopService(self)
        self._service_d = None
        return d


class IMagicFolderFilesystem(Interface):
    """
    An object that can make changes to the local filesystem
    magic-directory. It has a staging area to put files into so that
    there are no 'partial' files in the magic-folder.
    """

    def download_content_to_staging(remote_snapshot, tahoe_client):
        """
        Prepare the content by downloading it.

        :returns Deferred[FilePath]: the location of the downloaded
            content (or errback if the download fails).
        """

    def mark_overwrite(remote_snapshot, staged_content):
        """
        This snapshot is an overwrite. Move it from the staging area over
        top of the existing file (if any) in the magic-folder.

        :param FilePath staged_content: a local path to the downloaded
            content.
        """

    def mark_conflict(remote_snapshot, staged_content):
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

    def mark_delete(remote_snapshot):
        """
        Mark this snapshot as a delete. The existing magic-folder file
        shall be deleted.
        """


@attr.s
@implementer(service.IService)
class MagicFolderUpdaterService(service.Service):
    """
    Updates the local magic-folder when given locally-cached
    RemoteSnapshots. These RemoteSnapshot instance must have all
    relevant parents available (via the cache service).

    "Relevent" here means all parents unless we find a common
    ancestor.
    """
    _magic_fs = attr.ib(validator=provides(IMagicFolderFilesystem))
    _config = attr.ib(validator=instance_of(MagicFolderConfig))
    _remote_cache = attr.ib(validator=instance_of(RemoteSnapshotCacheService))
    tahoe_client = attr.ib() # validator=instance_of(TahoeClient))
    _queue = attr.ib(default=attr.Factory(DeferredQueue))

    def add_remote_snapshot(self, snapshot):
        """
        :returns Deferred: fires with None when this RemoteSnapshot has
            been processed (or errback if that fails).
        """
        d = Deferred()
        self._queue.put((snapshot, d))
        return d

    def startService(self):
        """
        Wait for a single item from the queue and process it, forever.
        """
        service.Service.startService(self)
        self._service_d = self._process_queue()

    def stopService(self):
        """
        Don't process queued items anymore.
        """
        d = self._service_d
        self._service_d.cancel()
        service.Service.stopService(self)
        self._service_d = None
        return d

    @inline_callbacks
    def _process_queue(self):
        """
        Wait for a single item from the queue and process it, forever.
        """
        while True:
            try:
                (snapshot, d) = yield self._queue.get()
                with start_action(action_type="downloader:modify_filesystem"):
                    yield self._process(snapshot)
                    d.callback(None)
            except CancelledError:
                break
            except Exception:
                d.errback(Failure())

    @inline_callbacks
    def _process(self, snapshot):
        """
        Internal helper.

        Determine the disposition of 'snapshot' and propose
        appropriate filesystem modifications. Once these are done,
        note the new snapshot-cap in our database and then push it to
        Tahoe (i.e. to our Personal DMD)
        """

        with start_action(action_type="downloader:updater:process") as action:
            local_path = self._config.magic_path.preauthChild(magic2path(snapshot.name))
            staged = yield self._magic_fs.download_content_to_staging(snapshot, self.tahoe_client)

            # check if we have this snapshot already .. it's possible
            # to have both local and remote snapshots.
            try:
                remote_cap = self._config.get_remotesnapshot(snapshot.name),
                # w/ no KeyError we have seen this before
                action.add_success_fields(remote=remote_snap.capability)
                try:
                    remote_snap = self._remote_cache.cached_snapshots[remote_cap]
                except KeyError:
                    raise RuntimeError(
                        "Internal inconsistency: remotesnapshot not in cache"
                    )
            except KeyError:
                remote_snap = None

            try:
                local_snap = self._config.get_local_snapshot(snapshot.name)
                action.add_success_fields(
                    local=local_snap.capability,
                )
            except KeyError:
                local_snap = None

            # note: if local_snap and remote_snap are both non-None
            # then remote_snap should be the ancestor of local_snap

            # XXX not dealing with deletes yet
            if local_path.exists():
                # we have something locally. if we have no remote yet,
                # it's a conflict (because we've not told anyone about
                # it)

                # (if we do have any locals, they will have this
                # remote_snap as an ancestor)
                existing_snap = remote_snap

                # we shouldn't even queue updates if we already match,
                # but double-check just in case
                if existing_snap is not None and existing_snap.capability == snapshot.capability:
                    return

                # do we have a common ancestor with the proposed change?
                ancestor = False
                q = deque([snapshot])
                while q and not ancestor:
                    snap = q.popleft()
                    for parent_cap in snap.parents_raw:
                        if existing_snap.capability == parent_cap:
                            ancestor = True
                            break
                        else:
                            q.append(self._remote_cache.cached_snapshots[parent_cap])
                action.add_success_fields(ancestor=ancestor)

                # whether we found a common ancestor or not
                # tells us if we've got an overwrite or
                # conflict
                if ancestor:
                    self._magic_fs.mark_overwrite(snapshot, staged)
                else:
                    self._magic_fs.mark_conflict(snapshot, staged)

            else:
                # there is no local file
                # XXX we don't handle deletes yet
                assert not local_snap and not remote_snap, "Internal inconsistency"
                self._magic_fs.mark_overwrite(snapshot, staged)

            # Note, if we crash here (after moving the file into place
            # but before noting that in our database) then we could
            # produce LocalSnapshots referencing the wrong
            # parent. This will be corrected when we re-start and make
            # it past this point again.

            # remember the last remote we've downloaded
            self._config.store_remotesnapshot(snapshot.name, snapshot)

            # XXX careful here, we still need something that makes
            # sure mismatches between remotesnapshots in our db and
            # the Personal DMD are reconciled .. that is, if we crash
            # here and/or can't update our Personal DMD we need to
            # retry later.
            yield self.tahoe_client.add_entry_to_mutable_directory(
                self._config.upload_dircap,
                snapshot.name,
                snapshot.capability.encode("ascii"),
                replace=True,
            )


@implementer(IMagicFolderFilesystem)
@attr.s
class LocalMagicFolderFilesystem(object):
    """
    Makes changes to a local directory.
    """

    magic_path = attr.ib(validator=instance_of(FilePath))
    staging_path = attr.ib(validator=instance_of(FilePath))

    @inlineCallbacks
    def download_content_to_staging(self, remote_snapshot, tahoe_client):
        """
        IMagicFolderFilesystem API
        """
        import hashlib
        h = hashlib.sha256()
        h.update(remote_snapshot.capability)
        staged_path = self.staging_path.child(h.hexdigest())
        with staged_path.open('wb') as f:
            yield tahoe_client.stream_capability(remote_snapshot.content_cap, f)
        returnValue(staged_path)

    def mark_overwrite(self, remote_snapshot, staged_content):
        """
        This snapshot is an overwrite. Move it from the staging area over
        top of the existing file (if any) in the magic-folder.

        :param FilePath staged_content: a local path to the downloaded
            content.
        """
        local_path = self.magic_path.preauthChild(magic2path(remote_snapshot.name))
        staged_content.moveTo(local_path)

    def mark_conflict(self, remote_snapshot, staged_content):
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
        local_path = self.magic_path.preauthChild(
            magic2path(remote_snapshot.name) + ".conflict-{}".format(remote_snapshot.author.name)
        )
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
        """
        """


@attr.s
@implementer(service.IService)
class DownloaderService(service.MultiService):
    """
    A service that periodically polls the Colletive DMD for new
    RemoteSnapshot capabilities to download.
    """

    _config = attr.ib()
    _participants = attr.ib()
    _remote_snapshot_cache = attr.ib(validator=instance_of(RemoteSnapshotCacheService))
    _folder_updater = attr.ib(validator=instance_of(MagicFolderUpdaterService))
    _tahoe_client = attr.ib()

    @classmethod
    def from_config(cls, name, config, participants, remote_snapshot_cache, folder_updater, tahoe_client):
        """
        Create a DownloaderService from the MagicFolder configuration.
        """
        return cls(
            config,
            participants,
            remote_snapshot_cache,
            folder_updater,
            tahoe_client,
        )

    def __attrs_post_init__(self):
        service.MultiService.__init__(self)
        self._folder_updater.setServiceParent(self)
        self._remote_snapshot_cache.setServiceParent(self)
        self._scanner = internet.TimerService(
            self._config.poll_interval,
            self._scan_collective,
        )
        self._scanner.setServiceParent(self)

    @inline_callbacks
    def _scan_collective(self):
        action = start_action(action_type="downloader:scan-collective")
        with action:
            try:
                people = yield self._participants.list()
            except SchemeNotSupported:
                # mostly a testing aid; if we provided an "empty"
                # Tahoe Client fake we get this error .. otherwise we
                # need a proper collective set up.
                people = []
            for person in people:
                Message.log(message_type="scan-participant", person=person.name, is_self=person.is_self)
                if person.is_self:
                    # we don't download from ourselves
                    continue
                files = yield self._tahoe_client.list_directory(person.dircap)
                action.add_success_fields(files=files)
                for fname, data in files.items():
                    snapshot_cap, metadata = data
                    fpath = self._config.magic_path.preauthChild(magic2path(fname))
                    relpath = "/".join(fpath.segmentsFrom(self._config.magic_path))
                    action.add_success_fields(
                        abspath=fpath.path,
                        relpath=relpath,
                    )
                    snapshot = yield self._remote_snapshot_cache.add_remote_capability(snapshot_cap)
                    # if this remote matches what we believe to be the
                    # latest, there is nothing to do .. otherwise, we
                    # have to figure out what to do
                    try:
                        our_snapshot_cap = self._config.get_remotesnapshot(snapshot.name)
                    except KeyError:
                        our_snapshot_cap = None
                    if snapshot.capability != our_snapshot_cap:
                        yield self._folder_updater.add_remote_snapshot(snapshot)
