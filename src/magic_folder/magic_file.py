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


def _last_one(things):
    """
    Used as a 'collector' for some Automat state transitions.
    :returns: the last thing in the iterable
    """
    return list(things)[-1]


@attr.s
class MagicFileFactory(object):
    """
    Creates MagicFile instances.

    Mostly this makes injecting dependenicies a bit easier. Other
    services that MagicFiles need access to are stored here so each
    MagicFile can just reference its factory.
    """
    _config = attr.ib()  # MagicFolderConfig
    _tahoe_client = attr.ib()
    _folder_status = attr.ib()
    _local_snapshot_service = attr.ib()
    _write_participant = attr.ib()

    _magic_files = attr.ib(default=attr.Factory(dict))  # relpath -> MagicFile

    def magic_file_for(self, path):
        """
        :returns: the MagicFile instance for path. This may create one
            first or use a cached one.
        """
        # this segmentsFrom call also ensures this relpath doesn't
        # 'escape' our magic-path
        relpath = u"/".join(path.segmentsFrom(self._config.magic_path))
        try:
            return self._magic_files[relpath]
        except KeyError:
            # XXX 'something' should be e.g. firing a couple
            # local_update()s at this if there are existing
            # LocalSnapshots for this relpath -- do we do that here,
            # or elsewhere? (probably here makes sense)
            mf = MagicFile(
                path,
                relpath,
                self,
            )
            self._magic_files[relpath] = mf

            # debugging -- might be nice to surface this ultimately as
            # a command-line option, and we JSON-serialize all
            # transitions to a file/FD provided?
            if True:
                def tracer(old_state, the_input, new_state):
                    print("{}: {} --[ {} ]--> {}".format(relpath, old_state, the_input, new_state))
                mf.set_trace(tracer)

            return mf


@attr.s
class MagicFile(object):
    """
    A single file in a single magic-folder
    """

    _path = attr.ib()  # FilePath
    _relpath = attr.ib()  # text, relative-path inside our magic-folder
    _factory = attr.ib(validator=attr.validators.instance_of(MagicFileFactory))

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
    def existing_local_snapshot(self, snapshot):
        """
        One or more LocalSnapshot instances already exist for this path.

        :param LocalSnapshot snapshot: the snapshot (which should be
            the 'youngest' if multiple linked snapshots exist).
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
        print("_begin_download")
        # queue a download -- inject a new input when done (whether
        # error or success)

    @_machine.output()
    def _cancel_download(self):
        """
        Stop an ongoing download
        """
        print("_cancel_download")

    @_machine.output()
    def _perform_remote_update(self, snapshot):
        """
        Resolve a remote update locally
        """
        print("_perform_remote_update", snapshot)

    @_machine.output()
    def _status_upload_queued(self):
        self._factory._folder_status.upload_queued(self._relpath)

    @_machine.output()
    def _status_upload_started(self):
        self._factory._folder_status.upload_started(self._relpath)

    @_machine.output()
    def _status_upload_finished(self):
        self._factory._folder_status.upload_finished(self._relpath)

    @_machine.output()
    def _status_download_started(self):
        self._factory._folder_status.download_started(self._relpath)

    @_machine.output()
    def _status_download_finished(self):
        self._factory._folder_status.download_finished(self._relpath)

    @_machine.output()
    def _create_local_snapshot(self):
        """
        Create a LocalSnapshot for this update
        """
        d = self._factory._local_snapshot_service.add_file(self._path)
        print("ADDFILE", d)

        def completed(snap):
            return self.snapshot_completed(snap)

        def bad(f):
            print("BAD", f)
        d.addCallback(completed)
        d.addErrback(bad)
        # errback? (re-try?)
        return d

    @_machine.output()
    def _begin_upload(self, snapshot):
        """
        Begin uploading a LocalSnapshot (to create a RemoteSnapshot)
        """

        assert snapshot is not None, "Internal inconsistency"

        @inline_callbacks
        def perform_upload():
            upload_started_at = time.time()
            print("BEGIN UP", snapshot)
            remote_snapshot = yield write_snapshot_to_tahoe(
                snapshot,
                self._factory._config.author,
                self._factory._tahoe_client,
            )
            print("REMOTE", remote_snapshot)
            snapshot.remote_snapshot = remote_snapshot
            yield self._factory._config.store_uploaded_snapshot(
                remote_snapshot.relpath,
                remote_snapshot,
                upload_started_at,
            )
            self.upload_completed(snapshot)

        d = perform_upload()
        def bad(f):
            print("BADDD", f)
        d.addErrback(bad)
#        d.addErrback(write_traceback)


    @_machine.output()
    def _update_personal_dmd(self, snapshot):
        """
        Update our personal DMD (after an upload)
        """

        @inline_callbacks
        def update_personal_dmd():
            remote_snapshot = snapshot.remote_snapshot
            assert remote_snapshot is not None, "remote-snapshot must exist"
            # update the entry in the DMD
            yield self._factory._write_participant.update_snapshot(
                snapshot.relpath,
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
            yield self._factory._config.delete_all_local_snapshots_for(snapshot.relpath)
            self.personal_dmd_updated(snapshot)
        d = update_personal_dmd()
        def bad(f):
            print("ADDDDD", f)
            return f
        d.addErrback(bad)
##        d.addErrback(write_traceback)

    @_machine.output()
    def _detected_remote_conflict(self, snapshot):
        """
        A remote conflict is detected
        """
        # note conflict, write conflict files

    @_machine.output()
    def _detected_local_conflict(self, new_content):
        """
        A local conflict is detected
        """
        # note conflict, write conflict files

    @_machine.output()
    def _queue_local_update(self, new_content):
        """
        """
        print("queue_local_update")

    @_machine.output()
    def _queue_remote_update(self, snapshot):
        """
        """
        print("queue_remote_update")

    @_machine.output()
    def _update_snapshot_cache(self, snapshot):
        """
        """
        print("update_snapshot_cache")
        # might not need this ...

    @_machine.output()
    def _check_for_work(self):
        """
        """
        print("_check_for_work")

    up_to_date.upon(
        remote_update,
        enter=downloading,
        outputs=[_status_download_started, _begin_download],
        collector=_last_one,
    )
    downloading.upon(
        download_completed,
        enter=updating_personal_dmd,
        outputs=[_perform_remote_update, _status_download_finished],
    )
    downloading.upon(
        remote_update,
        enter=downloading,
        outputs=[_queue_remote_update],
        collector=_last_one,
    )

    up_to_date.upon(
        local_update,
        enter=creating_snapshot,
        outputs=[_status_upload_queued, _create_local_snapshot],
        collector=_last_one,
    )
    creating_snapshot.upon(
        snapshot_completed,
        enter=uploading,
        outputs=[_status_upload_started, _begin_upload],
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
        collector=_last_one,
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
        collector=_last_one,
    )
    updating_personal_dmd.upon(
        personal_dmd_updated,
        enter=up_to_date,
        outputs=[_status_upload_finished, _update_snapshot_cache, _check_for_work]
    )

    up_to_date.upon(
        existing_local_snapshot,
        enter=uploading,
        outputs=[_status_upload_queued, _status_upload_started, _begin_upload],
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
        outputs=[_status_download_finished, _cancel_download],
        collector=_last_one,
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
        collector=_last_one,
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
