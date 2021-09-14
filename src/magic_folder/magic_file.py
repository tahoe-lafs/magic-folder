from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import time

import automat
import attr
from eliot import (
    write_traceback,
    start_action,
    current_action,
    Message,
)
from eliot.twisted import (
    inline_callbacks,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet import (
    reactor,
)

from .snapshot import (
    write_snapshot_to_tahoe,
)
from .util.file import (
    get_pathinfo,
)


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
    _remote_cache = attr.ib()
    _magic_fs = attr.ib()  # IMagicFolderFilesystem

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
                action=start_action(
                    action_type="magic_file",
                    relpath=relpath,
                )
            )
            self._magic_files[relpath] = mf

            # debugging -- might be nice to surface this ultimately as
            # a command-line option, and we JSON-serialize all
            # transitions to a file/FD provided?
            if True:
                def tracer(old_state, the_input, new_state):
                    with mf._action.context():
                        Message.log(
                            message_type="state_transition",
                            old_state=old_state,
                            trigger=the_input,
                            new_state=new_state,
                        )
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
    _action = attr.ib()

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
    def download_check_ancestor(self):
        """
        We've found a remote update; check its parentage
        """

    @_machine.state()
    def download_check_local(self):
        """
        We're about to make local changes; make sure a filesystem change
        didn't sneak in.
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
    def updating_personal_dmd_upload(self):
        """
        We are updating Tahoe state after an upload
        """

    @_machine.state()
    def updating_personal_dmd_download(self):
        """
        We are updating Tahoe state after a download
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
    def download_mismatch(self, snapshot, staged_path):
        """
        The local file does not match what we expect given database state
        """

    @_machine.input()
    def download_matches(self, snapshot, staged_path):
        """
        The local file (if any) matches what we expect given database
        state
        """

    @_machine.input()
    def remote_update(self, snapshot):
        """
        The file has a remote update.

        XXX should this be 'snapshots' for multiple participant updates 'at once'?
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
    def download_completed(self, snapshot, staged_path):
        """
        A remote Snapshot has been downloaded
        """

    @_machine.input()
    def download_error(self, snapshot):
        """
        An error occurred while downloading a snapshot
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

    @_machine.input()
    def _ancestor_is_us(self, snapshot, staged_path):
        """
        We are actually the ancestor of snapshot
        """

    @_machine.input()
    def _ancestor_matches(self, snapshot, staged_path):
        """
        snapshot is our ancestor
        """

    @_machine.input()
    def _ancestor_mismatch(self, snapshot, staged_path):
        """
        snapshot is not our ancestor
        """

    @_machine.output()
    def _pause_between_downloads(self, snapshot):
        """
        """
        print("pause between downloads")
        return deferLater(reactor, 5.0)

    @_machine.output()
    def _pause_between_uploads(self, new_content):
        print("pause between downloads")
        return deferLater(reactor, 5.0)

    @_machine.output()
    def _begin_download(self, snapshot):
        """
        Download a given Snapshot (including its content)
        """
        print("_begin_download")
        d = self._factory._magic_fs.download_content_to_staging(
            snapshot.relpath,
            snapshot.content_cap,
            self._factory._tahoe_client,
        )

        def downloaded(staged_path):
            print("downloaded", staged_path)
            self.download_completed(snapshot, staged_path)
        d.addCallback(downloaded)
        def bad(f):
            print("badbad", f)
            return f
        d.addErrback(bad)  # XXX FIXME
        return d

    @_machine.output()
    def _cancel_download(self):
        """
        Stop an ongoing download
        """
        print("_cancel_download")

    @_machine.output()
    def _check_local_update(self, snapshot, staged_path):
        """
        Detect a 'last minute' change by comparing the state of our local
        file to that of the database.

        In case of a brand new file we've never seen: the database
        will have no entry, and there is no local file.

        In case of an updated: the pathinfo of the file right now must
        match what's in the database.
        """
        print("_check_local_update", staged_path)

        try:
            current_pathstate = self._factory._config.get_currentsnapshot_pathstate(self._relpath)
        except KeyError:
            current_pathstate = None

        local_pathinfo = get_pathinfo(self._path)

        # we don't check for a LocalSnapshot existing, because that
        # should be impossible: the state-machine will take a
        # local_update() for anything in the "downloading" branch and
        # cause a conflict .. but for safety we can assert that.
        try:
            self._factory._config.get_local_snapshot(self._relpath)
            assert False, "unexpected local snapshot; state-machine inconsistency?"
        except KeyError:
            pass

        # now, determine if we've found a local update
        if current_pathstate is None:
            if local_pathinfo.exists:
                self.download_mismatch(snapshot, staged_path)
        else:
            # we've seen this file before so its pathstate should
            # match what we expect according to the database .. or
            # else some update happened meantime.
            if current_pathstate != local_pathinfo.state:
                self.download_mismatch(snapshot, staged_path)

        self.download_matches(snapshot, staged_path)

    @_machine.output()
    def _check_ancestor(self, snapshot):
        """
        Check if the ancestor for this remote update is correct or not.
        """
        try:
            remote_cap = self._factory._config.get_remotesnapshot(snapshot.relpath)
        except KeyError:
            remote_cap = None
        # if remote_cap is None, we've never seen this before (so the ancestor is always correct)
        if remote_cap is not None:
            ancestor = self._factory._remote_cache.is_ancestor_of(remote_cap, snapshot.capability)

            if not ancestor:
                # if the incoming remotesnapshot is actually an
                # ancestor of _our_ snapshot, then we have nothing
                # to do because we are newer
                if self._factory._remote_cache.is_ancestor_of(snapshot.capability, remote_cap):
                    return self._ancestor_of_us(snapshot, None)
                return self._ancestor_mismatch(snapshot, None)
        return self._ancestor_matches(snapshot, None)

    @_machine.output()
    def _perform_remote_update(self, snapshot, staged_path):
        """
        Resolve a remote update locally
        """
        print("_perform_remote_update", snapshot.relpath, staged_path)
        # there is a longer dance described in detail in
        # https://magic-folder.readthedocs.io/en/latest/proposed/magic-folder/remote-to-local-sync.html#earth-dragons-collisions-between-local-filesystem-operations-and-downloads
        # which may do even better at reducing the window for local
        # changes to get overwritten. Currently, that window is the 3
        # python statements between here and ".mark_overwrite()"

        if snapshot.content_cap is None:
            self._factory._magic_fs.mark_delete(snapshot)
        else:
            try:
                path_state = self._factory._magic_fs.mark_overwrite(
                    snapshot.relpath,
                    snapshot.metadata["modification_time"],
                    staged_path,
                )
            except OSError as e:
                self._factory._folder_status.error_occurred(
                    "Failed to overwrite file '{}': {}".format(snapshot.relpath, str(e))
                )
                raise

        # Note, if we crash here (after moving the file into place but
        # before noting that in our database) then we could produce
        # LocalSnapshots referencing the wrong parent. We will no
        # longer produce snapshots with the wrong parent once we
        # re-run and get past this point.

        # remember the last remote we've downloaded
        self._factory._config.store_downloaded_snapshot(
            snapshot.relpath, snapshot, path_state
        )

        # XXX careful here, we still need something that makes
        # sure mismatches between remotesnapshots in our db and
        # the Personal DMD are reconciled .. that is, if we crash
        # here and/or can't update our Personal DMD we need to
        # retry later.
        d = self._factory._write_participant.update_snapshot(
            snapshot.relpath,
            snapshot.capability.encode("ascii"),
        )

        def updated_snapshot(arg):
            print("UP", arg)
            self._factory._config.store_currentsnapshot_state(
                snapshot.relpath,
                path_state,
            )
            self.personal_dmd_updated(snapshot)
        d.addCallback(updated_snapshot)
        return d

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
            print("peform_upload", snapshot.relpath)
            with self._action.context() as action:
                upload_started_at = time.time()
                Message.log(message_type="uploading")

                remote_snapshot = yield write_snapshot_to_tahoe(
                    snapshot,
                    self._factory._config.author,
                    self._factory._tahoe_client,
                )
                Message.log(remote_snapshot=remote_snapshot)
                snapshot.remote_snapshot = remote_snapshot
                yield self._factory._config.store_uploaded_snapshot(
                    remote_snapshot.relpath,
                    remote_snapshot,
                    upload_started_at,
                )
                self.upload_completed(snapshot)
                Message.log(message_type="upload_completed")

        d = perform_upload()
        def bad(f):
            print("error; waiting 5 seconds")
            x = deferLater(reactor, 5.0)
            print("X", x)
            def foo(*args):
                print("LATER", args)
                return perform_upload()
            x.addCallback(foo)
            x.addErrback(bad)
            return x
        d.addErrback(bad)
        return d
#        d.addErrback(write_traceback)

    @_machine.output()
    def _mark_download_conflict(self, snapshot, staged_path):
        """
        Mark a conflict for this remote snapshot
        """
        print("_mark_download_conflict")

    @_machine.output()
    def _update_personal_dmd_upload(self, snapshot):
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
        print("_detected_remote_conflict")
        # note conflict, write conflict files

    @_machine.output()
    def _detected_local_conflict(self, new_content):
        """
        A local conflict is detected
        """
        print("_detected_local_conflict")
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
    def _check_for_work(self):
        """
        """
        print("_check_for_work")

    up_to_date.upon(
        remote_update,
        enter=download_check_ancestor,
        outputs=[_check_ancestor],
        collector=_last_one,
    )

    download_check_ancestor.upon(
        _ancestor_is_us,
        enter=up_to_date,
        outputs=[],
    )
    download_check_ancestor.upon(
        _ancestor_mismatch,
        enter=conflicted,
        outputs=[_mark_download_conflict],
    )
    download_check_ancestor.upon(
        _ancestor_matches,
        enter=downloading,
        outputs=[_status_download_started, _begin_download],
        collector=_last_one,
    )

    downloading.upon(
        download_completed,
        enter=download_check_local,
        outputs=[_check_local_update],
    )
    downloading.upon(
        download_error,
        enter=downloading,
        outputs=[_pause_between_downloads, _begin_download],
    )
    downloading.upon(
        remote_update,
        enter=downloading,
        outputs=[_queue_remote_update],
        collector=_last_one,
    )

    download_check_local.upon(
        download_matches,
        enter=updating_personal_dmd_download,
        outputs=[_perform_remote_update],
    )
    download_check_local.upon(
        download_mismatch,
        enter=conflicted,
        outputs=[_mark_download_conflict, _status_download_finished],
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
        enter=updating_personal_dmd_upload,
        outputs=[_update_personal_dmd_upload],
    )
    uploading.upon(
        local_update,
        enter=uploading,
        outputs=[_queue_local_update],
        collector=_last_one,
    )

    updating_personal_dmd_upload.upon(
        personal_dmd_updated,
        enter=up_to_date,
        outputs=[_status_upload_finished, _check_for_work]
    )
    updating_personal_dmd_download.upon(
        personal_dmd_updated,
        enter=up_to_date,
        outputs=[_status_download_finished, _check_for_work]
    )

    up_to_date.upon(
        existing_local_snapshot,
        enter=uploading,
        outputs=[_status_upload_queued, _status_upload_started, _begin_upload],
    )

    # XXX what's this state for? did we hit it somehow in testing?
    conflicted.upon(
        personal_dmd_updated,
        enter=conflicted,
        outputs=[]
    )


    # XXX probably more error-cases!
    creating_snapshot.upon(
        remote_update,
        enter=conflicted,
        outputs=[_detected_remote_conflict],
        collector=_last_one,
    )
    uploading.upon(
        remote_update,
        enter=conflicted,
        outputs=[
            _update_personal_dmd_upload,  # still want to do this ..
            _detected_remote_conflict,
        ],
        collector=_last_one,
    )
    downloading.upon(
        local_update,
        enter=conflicted,
        outputs=[_status_download_finished, _cancel_download],
        collector=_last_one,
    )
    updating_personal_dmd_download.upon(
        remote_update,
        enter=updating_personal_dmd_download,
        outputs=[_queue_remote_update],
        collector=_last_one,
    )
    updating_personal_dmd_download.upon(
        local_update,
        enter=conflicted,
        outputs=[_detected_local_conflict],
        collector=_last_one,
    )

    updating_personal_dmd_upload.upon(
        remote_update,
        enter=conflicted,
        outputs=[_detected_remote_conflict],
        collector=_last_one,
    )
    updating_personal_dmd_upload.upon(
        local_update,
        enter=updating_personal_dmd_upload,
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
        collector=_last_one,
    )
    conflicted.upon(
        local_update,
        enter=conflicted,
        outputs=[],  # nothing, likely: user messing with resolution file?
        collector=_last_one,
    )
