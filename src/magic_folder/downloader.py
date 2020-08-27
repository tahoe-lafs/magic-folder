

from twisted.application import service

"""
XXX Notes:
- local state needs to note the last Snapshot we had for each name
- this state is synchronized to our Personal DMD when we're online
- state needs to note the last
"""


@attr.s
@implementer(service.IService)
class RemoteSnapshotCacheService(service.Service):
    """
    When told about Snapshot capabilities we download them.
    """
    _file_modifier = attr.ib()
    _queue = attr.ib(default=attr.Factory(DeferredQueue))
    _config = attr.ib()  # MagicFolderConfig so we can store our state

    def add_remote_capability(self, snapshot_cap):
        """
        Add the given immutable Snapshot capability to our queue. Ensure
        all parents are also in our cache.

        :param bytes snapshot_cap: an immutable directory capability-string

        :returns Deferred[RemoteSnapshot]: a Deferred that fires with
            the RemoteSnapshot when this item **and all its parents**
            are downloaded (or errbacks if the download fails).

        :raises QueueOverflow: if our queue is full
        """


@attr.s
@implementer(service.IService)
class MagicFolderUpdaterService(service.Service):
    """
    Updates the local magic-folder when given locally-cached
    RemoteSnapshots (with all parents available).
    """
    _magic_fs = attr.ib(validator=provides(IMagicFolderFilesystem))
    _queue = attr.ib(default=attr.Factory(DeferredQueue))

    def add_remote_snapshot(self, snapshot):
        """
        :returns Deferred: fires with None when this RemoteSnapshot has
            been processed (or errback if that fails).
        """

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


class IMagicFolderFilesystem(Interface):
    """
    An object that can make changes to the local filesystem
    magic-directory. It has a staging area to put files into so that
    there are no 'partial' files in the magic-folder.
    """

    def download_content_to_staging(remote_snapshot):
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
        a file named `.conflict-$NAME` where `$NAME` is the author of
        the conflicting snapshot.

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



@implements(IMagicFolderFilesystem)
@attr.s
class LocalMagicFolderFilesystem(object):
    """
    Makes changes to a local directory.
    """

    magic_path = attr.ib(validator=instance_of(FilePath))
    staging_path = attr.ib(validator=instance_of(FilePath))


@implements(IMagicFolderFilesystem)
class InMemoryMagicFolderFilesystem(object):
    """
    Simply remembers the changes that would be made to a local
    filesystem. Generally for testing.
    """

    def __init__(self):
        """
        """



@implementer(service.IService)
class DownloaderService(service.Service):
    """
    A service that periodically polls the Colletive DMD for new
    RemoteSnapshot capabilities to download.
    """

    _remote_snapshot_cache = attr.ib(validator=instance_of(RemoteSnapshotCacheService))
    _folder_updater = attr.ib(validator=instance_of(MagicFolderUpdaterService))
    _tahoe_client = attr.ib()

    @classmethod
    def from_config(cls, clock, name, config, remote_snapshot_cache, tahoe_client):
        """
        Create an DownloaderService from the MagicFolder configuration.
        """

    # LoopingCall:
    #  - download Collective DMD
    #      - for each name, download Personal DMD
    #          - for each file:
    #              - if capability doesn't match ours:
    #                  - snap = await _remote_snapshot_cache.add_remote_capability(cap)
    #                  - (snap and all its parents will be cached now)
    #                  - _folder_updater.add_remote_snapshot(snap)
    #
    # If any of the above fails, we will resume on re-start: we will
    # discover the 'new' capability again in the same way ("their"
    # capability doesn't match ours) and the only difference will be
    # that the snapshot-cache may not have to download anything .. and
    # then the snapshot wil still be passed to the "updater" queue.
