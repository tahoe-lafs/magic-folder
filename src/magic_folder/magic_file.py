from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import time

from eliot import (
    write_traceback,
)
from eliot.twisted import (
    inline_callbacks,
)

from .snapshot import (
    write_snapshot_to_tahoe,
)

# i think this is "too pessimistic" on local updates
#  - if we're "doing a local update" and discover another local change, that's fine (we just queue it)
#  - if we're "doing a local update" and discover a remote change, conflict (always?)
#  - if we're "doing a remote update" and discover a local change: conflict
#  - if we're "doing a remote update" a discover a remote change: maybe-conflict? (i.e. wrong ancestor?)


import automat
import attr


@attr.s
class MagicFile(object):
    """
    A single file in a single magic-folder
    """

    _config = attr.ib()  # a MagicFolderConfig instance
    _folder_status = attr.ib()
    _local_snapshot_service = attr.ib()
    _path = attr.ib()  # FilePath
    _relpath = attr.ib()  # text, relative-path inside our magic-folder

    _machine = automat.MethodicalMachine()

    # debug
    set_trace = _machine._setTrace

    @_machine.state(initial=True)
    def up_to_date(self):
        """
        This file is up-to-date (our local state matches all remotes and
        our Personal DMD matches our local state.
        """

    @_machine.state()
    def downloading(self):
        """
        We are retrieving a remote update
        """

    @_machine.state()
    def creating_snapshot(self):
        """
        We are creating a LocalSnapshot for a given update
        """

    @_machine.state()
    def uploading(self):
        """
        We are uploading a LocalSnapshot
        """

    @_machine.state()
    def updating_personal_dmd(self):
        """
        We are updating Tahoe state
        """

    @_machine.state()
    def conflicted(self):
        """
        There is a conflit that must be resolved
        """

    @_machine.input()
    def local_update(self, new_content):
        """
        The file is changed locally (created or updated or deleted)

        XXX if new_content is None, it's a delete -- or maybe we want
        a different kind of input?
        """

    @_machine.input()
    def remote_update(self, snapshot):
        """
        The file has a remote update

        XXX this should probably be 'snapshots' for multiple participants
        XXX does this include deletes?
        """

    @_machine.input()
    def download_completed(self, snapshot):
        """
        A remote Snapshot has been downloaded
        """

    @_machine.input()
    def snapshot_completed(self, snapshot):
        """
        A LocalSnapshot for this update is created
        """

    @_machine.input()
    def upload_completed(self, snapshot):
        """
        A LocalSnapshot has been turned into a RemoteSnapshot
        """

    @_machine.input()
    def personal_dmd_updated(self, snapshot):
        """
        An update to our Personal DMD has been completed
        """

    @_machine.input()
    def conflict_resolution(self, snapshot):
        """
        A conflicted file has been resolved
        """

    @_machine.output()
    def _begin_download(self, snapshot):
        """
        Download a given Snapshot (including its content)
        """

    @_machine.output()
    def _cancel_download(self):
        """
        Stop an ongoing download
        """

    @_machine.output()
    def _perform_remote_update(self, snapshot):
        """
        Resolve a remote update locally
        """

    @_machine.output()
    def _create_local_snapshot(self):
        """
        Create a LocalSnapshot for this update
        """
        self._folder_status.upload_queued(self._relpath)
        d = self._local_snapshot_service.add_file(self._path)
        d.addCallback(
            lambda snap: self.snapshot_completed(snap)
        )
        def bad(f):
            print("BAD", f)
        d.addErrback(bad)
        # errback? (re-try?)
        return d

    @_machine.output()
    def _begin_upload(self, snapshot):
        """
        Begin uploading a LocalSnapshot (to create a RemoteSnapshot)
        """

        @inline_callbacks
        def perform_upload():
            upload_started_at = time.time()
            remote_snapshot = yield write_snapshot_to_tahoe(
                snapshot,
                self._local_author,
                self._tahoe_client,
            )
            snapshot.remote_snapshot = remote_snapshot
            yield self._config.store_uploaded_snapshot(relpath, remote_snapshot, upload_started_at)
            self.upload_completed(snapshot)

        d = perform_upload()
        def bad(f):
            print("BADDD", f)
        d.addErrback(bad)
#        d.addErrback(write_traceback)


    @_machine.output()
    def _update_personal_dmd(self, snapshot):
        """
        Update our personal DMD
        """
        def update_personal_dmd():
            remote_snapshot = snapshot.remote_snapshot
            assert remote_snapshot is not None, "remote-snapshot must exist"
            # update the entry in the DMD
            yield self._write_participant.update_snapshot(
                relpath,
                remote_snapshot.capability,
            )
            # if removing the stashed content fails here, we MUST move on
            # to delete the LocalSnapshot because we may not be able to
            # re-create the Snapshot (maybe the content is "partially
            # deleted"?
            if snapshot.content_path is not None:
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

            # XXX FIXME must change db to delete just the one snapshot...
            # Remove the LocalSnapshot from the db.
            yield self._config.delete_all_local_snapshots_for(relpath)
            self.personal_dmd_updated(snapshot)
        d = update_personal_dmd()
        d.addErrback(write_traceback)

    @_machine.output()
    def _detected_remote_conflict(self, snapshot):
        """
        A remote conflict is detected
        """

    @_machine.output()
    def _detected_local_conflict(self, new_content):
        """
        A local conflict is detected
        """

    @_machine.output()
    def _queue_local_update(self, new_content):
        """
        """

    @_machine.output()
    def _queue_remote_update(self, snapshot):
        """
        """

    @_machine.output()
    def _update_snapshot_cache(self, snapshot):
        """
        """
        # might not need this ...

    @_machine.output()
    def _check_for_work(self):
        """
        """

    up_to_date.upon(
        remote_update,
        enter=downloading,
        outputs=[_begin_download],
    )
    downloading.upon(
        download_completed,
        enter=updating_personal_dmd,
        outputs=[_perform_remote_update, _update_personal_dmd],
    )
    downloading.upon(
        remote_update,
        enter=downloading,
        outputs=[_queue_remote_update]
    )

    up_to_date.upon(
        local_update,
        enter=creating_snapshot,
        outputs=[_create_local_snapshot],
    )
    creating_snapshot.upon(
        snapshot_completed,
        enter=uploading,
        outputs=[_begin_upload],
    )
    # XXX actually .. we should maybe re-start this snapshot? This
    # means we've found a change _while_ we're trying to create the
    # snapshot .. what if it's like half-created?
    # (hmm, should be impossible if we wait for the snapshot in the
    # scanner...but window for the API to hit it still ..)
    creating_snapshot.upon(
        local_update,
        enter=creating_snapshot,
        outputs=[_queue_local_update],
    )
    uploading.upon(
        upload_completed,
        enter=updating_personal_dmd,
        outputs=[_update_personal_dmd],
    )
    uploading.upon(
        local_update,
        enter=uploading,
        outputs=[_queue_local_update],
    )
    updating_personal_dmd.upon(
        personal_dmd_updated,
        enter=up_to_date,
        outputs=[_update_snapshot_cache, _check_for_work]
    )

    conflicted.upon(
        personal_dmd_updated,
        enter=conflicted,
        outputs=[_update_snapshot_cache]
    )


    # XXX probably more error-cases!
    creating_snapshot.upon(
        remote_update,
        enter=conflicted,
        outputs=[_detected_remote_conflict],
    )
    uploading.upon(
        remote_update,
        enter=conflicted,
        outputs=[
            _update_personal_dmd,  # still want to do this ..
            _detected_remote_conflict,
        ],
    )
    downloading.upon(
        local_update,
        enter=conflicted,
        outputs=[_cancel_download]
    )
    updating_personal_dmd.upon(
        remote_update,
        enter=conflicted,
        outputs=[_detected_remote_conflict],
    )
    updating_personal_dmd.upon(
        local_update,
        enter=updating_personal_dmd,
        outputs=[_queue_local_update],
    )


    conflicted.upon(
        conflict_resolution,
        enter=uploading,
        outputs=[_begin_upload],
    )
    conflicted.upon(
        remote_update,
        enter=conflicted,
        outputs=[],  # probably want to .. do something? remember it?
    )
    conflicted.upon(
        local_update,
        enter=conflicted,
        outputs=[],  # nothing, likely: user messing with resolution file?
    )
