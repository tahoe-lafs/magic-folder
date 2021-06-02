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
    start_action,
    start_task,
    Message,
)

from twisted.application import service
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.task import (
    LoopingCall,
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

from .tahoe_client import (
    TahoeClient,
)
from .config import (
    MagicFolderConfig,
)
from .magicpath import (
    path2magic,
    magic2path,
)
from .snapshot import (
    create_snapshot_from_capability,
)


"""
XXX Notes:
- local state needs to note the last Snapshot we had for each name (does it?)
- this state is synchronized to our Personal DMD when we're online

Questions:
- Do we want an Automat state-machine here?

  Conceptually, each "path" in our magic-folder has a state-machine as it goes from waiting/updating/conflicted/etc (maybe more or less states, but ..)


So, a magic-folder has remote-snapshots in its database.
These are name -> (magic_path, capabilitiy-string).

Given a "name", how do we determine if we need to download it?
  -> want a "last downloaded" or "on-disk state" or something
      - corresponds to the cap-string of the current stuff?
      - (what if we have a LocalSnapshot or two in the queue?)
  -> currently, the uploader creates a LocalSnapshot and then (eventually)
     it is uploaded, the localsnapshot is deleted (and becomes a remote snapshot)
    -> this process should note "on-disk state = capability-string"
    -> does "on-disk state" say "null" or something before upload?
      - maybe-equivalently: if we have pending LocalSnapshots for a
        thing, we don't do downloads on it? (no, that can't be right
        .. how would we learn if someone else updated meantime?)


Recovery:
 - the only real use-case we have where the downloader would do
   anything at all is recovery (until there are invites).
 - "magic-folder recover ..."
 - ^ gives us back information previous dumped. For example, we could
    - use "list" subcommand with --include-secret-information and
    - --json:

     {
        "name": "default",                                                                                        "author": {
            "name": "bob",
            "signing_key": "ZMQY4Z2N3HAYFRXHN4U3M6EE3SLV6DXEMWKS6TH2I6TVZW64PLAA====",
            "verify_key": "TO4QG6VEH5FLYXSW3KQFYLXGKU4R7722FCU2T6Q65U3YZP4QD7TA===="
        },
        "upload_dircap": "URI:DIR2:pbrtrmm5wmt6a3xzklcnlhp5hq:p7knzkbvc3zeyg5xzpbdzp3vhmy7soqen2b5xldfdpex
hqgffgiq",
        "poll_interval": 60,
        "is_admin": true,
        "collective_dircap": "URI:DIR2:hz46fi2e7gy6i3h4zveznrdr5q:i7yc4dp33y4jzvpe5jlaqyjxq7ee7qj2scouolum
rfa6c7prgkvq",
        "stash_path": "/home/meejah/work/leastauthority/src/magic-folder/bob-magic/default/stash",               "magic_path": "/home/meejah/work/leastauthority/src/magic-folder/bobs-magic-folder"
    }

 - okay, so we use the above information to create a new (but empty!)
   magic-folder configured as above.
 - assuming "bobs-magic-folder" is emtpy (we're recovering after all .. assume clean slate?)
 - there will be zero snapshots in the database (no remote, no local)
 - so, downloader goes exploring.
    - downloads the "collective dircap"
    - finds one entry: ours
    - downloads (via read-cap) that entry
    - finds .. some stuff

 (HANG ON, it's kind of weird to download our *own* collective .. only
 in the recovery state would it not match, and checking it feels wrong
 .. e.g. what if we deleted a file? Then if the downloader runs before
 the uploader it might say "oh, wait, we need to update this missing
 file!")

    - okay, so maybe it's better if in the "recovery" case what we do instead is:
      - configure new folder "very much like" our old one, BUT:
      - we have a new upload dircap
      - we add our old / recovered one as another participant (albeit
        this participant will never update)
        (we need to be admin for this, but that's fine because we just created the folder)
      - now the downloader can proceed as normal (our backup is just another folder participant)

    - maybe the HTTP API is just "add participant" (directly). No
      invite, we already have all the seekrit information we need.

"""


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
    tahoe_client = attr.ib()
    folder_config = attr.ib()
    _clock = attr.ib()  # IReactor
    cached_snapshots = attr.ib(default=attr.Factory(dict))
    _queue = attr.ib(default=attr.Factory(DeferredQueue))

    @classmethod
    def from_config(cls, clock, config, tahoe_client):
        """
        Create an RemoteSnapshotCacheService from the MagicFolder
        configuration.
        """
        return cls(tahoe_client, config, clock)

    def add_remote_capability(self, snapshot_cap):
        """
        Add the given immutable Snapshot capability to our queue.

        When this queue item is processed, we download the Snapshot,
        verify the signature then download the metadata.

        We download parents until we find a common ancestor .. meaning
        we keep downloading parents until we find one that matches the
        remotesnapshot in our local database. If we have no entry for
        this Snapshot we download all parents (which will also be the
        case if there is no common ancestor).

        :param bytes snapshot_cap: an immutable directory capability-string

        :returns Deferred[RemoteSnapshot]: a Deferred that fires with
            the RemoteSnapshot when this item has been processed (or
            errbacks if any of the downloads fails).

        :raises QueueOverflow: if our queue is full
        """
        Message.log(message_type="cache-service:add-remote-capability", snapshot_cap=snapshot_cap)
        d = Deferred()
        self._queue.put((snapshot_cap, d))
        return d

    def startService(self):
        """
        Start a periodic loop that looks for work and does it.
        """
        with start_action(message_type="cache-service:start"):
            service.Service.startService(self)
            self._service_d = self._process_queue()
            Message.log(starting=True)

            def log(f):
                print("fatal error")
                print(f)
                return None
            self._service_d.addErrback(log)

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
    tahoe_client = attr.ib(validator=instance_of(TahoeClient))
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
        Start a periodic loop that looks for work and does it.
        """
        service.Service.startService(self)
        self._service_d = self._process_queue()

        def log(f):
            print("fatal error")
            print(f)
            return None
        self._service_d.addErrback(log)

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
                # can we reach the cache service from here?
                remote_snap = yield create_snapshot_from_capability(
                    self._config.get_remotesnapshot(snapshot.name),
                    self.tahoe_client,
                )
                action.add_success_fields(
                    remote=remote_snap.capability,
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
                # if we have a valid local_snap or remote_snap that
                # means we've uploaded this file at least once; if we
                # share a common ancestor with the proposed change
                # then it's an update. If local_snap is valid, it will
                # be a newer one than remote_snap
                existing_snap = local_snap or remote_snap

                # do we have a common ancestor with the proposed change?
                ancestor = False
                q = deque([snapshot])
                while q and not ancestor:
                    snap = q.popleft()
                    if existing_snap.capability in snap.parents_raw:
                        ancestor = True
                    else:
                        q.extend(snap.parents_raw)
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

    # LoopingCall:
    #  - snap = await _queue.get()
    #  - decide what sort of change we have
    #  - if overwrite: content_path = magic_fs.download_content_to_staging(snap)
    #  - open state-db write transaction
    #      - write new local state (e.g. update our "name" to point at snapshot, remove name)
    #      - column for "did we write to tahoe yet?" marked False [*]
    #      - if conflict: magic_fs.mark_conflict(snap, content_path)
    #      - if overwrite: magic_fs.mark_overwrite(snap, content_path)
    #      - if delete: magic_fs.mark_delete(snap)
    #  - if db transaction fails, delete content_path
    #
    #  - link the snapshot capability into our Personal DMD
    #  - change "wrote to tahoe" to true
    #
    #  XXX do we want that last bit .. "somewhere else"? like, a
    #  separate service maybe? Thinking is that: on re-start we fill
    #  its queue with "rows of the database that say 'we didn't write
    #  to tahoe yet'" like what would happen if we died or failed
    #  during the "write new snapshot capability to Personal DMD"
    #  step...
    #
    # [*] or do we not want that column at all -- we could deduce it
    # by examining our own Personal DMD.


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
        raise NotImplemented()


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
class DownloaderService(service.Service):
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
    def from_config(cls, clock, name, config, participants, remote_snapshot_cache, folder_updater, tahoe_client):
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


    def startService(self):

        @inlineCallbacks
        def log_errors():
            try:
                yield self._scan_collective()
            except Exception as e:
                print("bad: {}".format(e))
                print(Failure())

        self._processing_loop = LoopingCall(log_errors)
#            self._scan_collective,
#        )
        self._processing = self._processing_loop.start(self._config.poll_interval, now=True)

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

    @inline_callbacks
    def _scan_collective(self):
        action = start_action(action_type="downloader:scan-collective")
        with action:
            people = yield self._participants.list()
            for person in people:
                Message.log(message_type="scan-participant", person=person.name, is_self=person.is_self)
                if person.is_self:
                    # if we keep the local remotesnapshot cache (in our
                    # config db) then this probably ought to check that
                    # it's correct .. maybe not ever poll, but ..
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
                    yield self._folder_updater.add_remote_snapshot(snapshot)

    # LoopingCall:
    #  - download Collective DMD
    #      - for each name, download Personal DMD
    #          - for each file:
    #              - if capability doesn't match ours:
    #                  - snap = await _remote_snapshot_cache.add_remote_capability(cap)
    #                  - (snap and all its parents will be cached now)
    #                  - _folder_updater.add_remote_snapshot(snap)
    #
    # "doesn't match ours": where 'ours' is .. what we have in our
    #     database for "name -> remotesnapshot"? (maybe: *unless* we also
    #     have a localsnapshot for that name?)
    #
    # If any of the above fails, we will resume on re-start: we will
    # discover the 'new' capability again in the same way ("their"
    # capability doesn't match ours) and the only difference will be
    # that the snapshot-cache may not have to download anything .. and
    # then the snapshot wil still be passed to the "updater" queue.
    #
    # re-thinking snapshot-cache: maybe it can just be in-memory (for
    # now? maybe forever?) .. it's "a cache" anyway, and I think only
    # has to "survive" long enough for us to decide "yes, we need to
    # download that content" .. then we download that content, pass it
    # to the "updater" which applies to filesystem, updates our
    # database and updates the collective DMD
    #
    # WARNING: that "update the collective DMD" is a tahoe operation,
    # so we'll have to remember "we wanted to do this" in case we
    # can't do tahoe stuff right then ... :/ ... and also keep retrying
