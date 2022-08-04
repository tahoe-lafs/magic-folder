import itertools

import automat
import attr
from eliot import (
    write_traceback,
    write_failure,
    start_action,
    Message,
)
from eliot.twisted import (
    inline_callbacks,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.internet.defer import (
    Deferred,
    DeferredList,
    DeferredSemaphore,
    maybeDeferred,
    succeed,
    CancelledError,
)
from twisted.web.client import (
    ResponseNeverReceived,
)
from twisted.application.internet import (
    backoffPolicy,
)

from .util.file import (
    get_pathinfo,
)
from .downloader import (
    BackupRetainedError,
)


def _last_one(things):
    """
    Used as a 'collector' for some Automat state transitions. Usually
    the only interesting object comes from the final output (and using
    this collector indicates that).

    :returns: the last thing in the iterable
    """
    return list(things)[-1]


def _delay_sequence(max_delay=10.0):
    """
    Internal helper. Produces a sequence of delays (in seconds) for
    use with retries
    """
    delay_function = backoffPolicy(
        maxDelay=max_delay,
    )
    for attempt in itertools.count():
        yield delay_function(attempt)


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
    _uploader = attr.ib()
    _write_participant = attr.ib()
    _remote_cache = attr.ib()
    _magic_fs = attr.ib()  # IMagicFolderFilesystem
    _synchronous = attr.ib(default=False)  # if True, _call_later doesn't use reactor

    _magic_files = attr.ib(default=attr.Factory(dict))  # relpath -> MagicFile
    _download_parallel = attr.ib(default=attr.Factory(lambda: DeferredSemaphore(5)))
    _delays = attr.ib(default=attr.Factory(list))
    _logger = attr.ib(default=None)  # mainly for tests

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
            # upon scanner startup, .local_snapshot_exists() inputs
            # are fed to this which causes it to start uploading.
            mf = MagicFile(
                path,
                relpath,
                self,
                action=start_action(
                    action_type="magic_file",
                    relpath=relpath,
                    logger=self._logger,
                ),
            )
            if self._synchronous:
                mf._call_later = lambda f, *args, **kw: f(*args, **kw)
                mf._delay_later = lambda f, *args, **kw: succeed("synchronous: no retry")

            # initialize "conflicted" state for this file
            if self._config.list_conflicts_for(relpath):
                mf._existing_conflict()

            # TODO: scanner ("or something") should check if our
            # current_snapshot notion of this file matches our
            # Personal DMD (if not: update them personal DMD because
            # we missed that step in the state-machine last time we
            # ran)

            # cache this MagicFile
            self._magic_files[relpath] = mf

            # debugging -- might be nice to surface this ultimately as
            # a command-line option
            if True:  # _debug_state_machine
                def tracer(old_state, the_input, new_state):
                    with mf._action.context():
                        Message.log(
                            message_type="state_transition",
                            old_state=old_state,
                            trigger=the_input,
                            new_state=new_state,
                            relpath=relpath,
                        )
                    # print("{}: {} --[ {} ]--> {}".format(relpath, old_state, the_input, new_state))
                mf.set_trace(tracer)

            return mf

    def cancel(self):
        """
        Cancel any files we know about (e.g. service is shutting down)
        """
        for d in self._delays:
            d.cancel()
        return self.finish()

    def finish(self):
        """
        Mostly for testing, this will yield on .when_idle() for every
        MagicFile we know about
        """
        idles = [
            mf.when_idle()
            for mf in self._magic_files.values()
        ]
        return DeferredList(idles)


@attr.s
class MagicFile(object):
    """
    A single file in a single magic-folder.

    The API methods here ultimately drive a state-machine implemented
    using the Automat library. This will automatically produce Dot
    diagrams describing the state-machine. To produce and see the
    diagram:

        automat-visualize magic_folder.magic_file
        feh -ZF .automat_visualize/magic_folder.magic_file.MagicFile._machine.dot.png

    When debugging (potential) issues with the state-machine, it is
    useful to turn on tracing (or see the Eliot logs which also logs
    the state-transitions). See _debug_state_machine above.

    If the outcome of some operate is to inject a new input into the
    state-machine, it should be done with self._call_later(...)
    This allows the state transition to "happen fully" (i.e. any
    following outputs run) before a new input is injected. For
    testing, _call_later() can be made synchronous.
    """

    _path = attr.ib()  # FilePath
    _relpath = attr.ib()  # text, relative-path inside our magic-folder
    _factory = attr.ib(validator=attr.validators.instance_of(MagicFileFactory))
    _action = attr.ib()
    _queue_local = attr.ib(default=attr.Factory(list))
    _queue_remote = attr.ib(default=attr.Factory(list))
    _is_working = attr.ib(default=None)

    # to facilitate testing, sometimes we _don't_ want to use the real
    # reactor to wait one turn, or to delay for retry purposes.
    _call_later = attr.ib(default=None)  # Callable to schedule work at least one reactor turn later
    _delay_later = attr.ib(default=None)  # Callable to return a Deferred that fires "later"

    _machine = automat.MethodicalMachine()

    # debug
    set_trace = _machine._setTrace

    def __attrs_post_init__(self):
        from twisted.internet import reactor

        if self._call_later is None:

            def next_turn(f, *args, **kwargs):
                return reactor.callLater(0, f, *args, **kwargs)
            self._call_later = next_turn

        if self._delay_later is None:

            def delay(seconds, f, *args, **kwargs):
                d = deferLater(reactor, seconds, f, *args, **kwargs)
                self._factory._delays.append(d)

                def remove(arg):
                    self._factory._delays.remove(d)
                    return arg
                d.addBoth(remove)
                return d
            self._delay_later = delay

    # these are API methods intended to be called by other code in
    # magic-folder

    def create_update(self):
        """
        Creates a new local change, reading the content from our (local)
        file (regardless of whether it has 'actually' changed or
        not). If the file doesn't exist (any more), a 'deletion'
        snapshot is created. The file contents are stashed and the
        state-database updated before the returned Deferred fires.

        Eventually, the snapshot will be uploaded. Even if our process
        re-starts, the LocalSnapshot will be recovered from local
        state and further attempts to upload will be made.

        :returns Deferred: fires with None when our local state is
            persisted. This is before the upload has occurred.
        """
        return self._local_update()

    def found_new_remote(self, remote_snapshot):
        """
        A RemoteSnapshot that doesn't match our existing database entry
        has been found. It will be downloaded and applied (possibly
        resulting in conflicts).

        :param RemoteSnapshot remote_snapshot: the newly-discovered remote
        """
        self._remote_update(remote_snapshot)
        return self.when_idle()

    def local_snapshot_exists(self, local_snapshot):
        """
        State describing a LocalSnapshot exists. This should be the
        'youngest' one if there are multiple snapshots for this file.

        :returns None:
        """
        d = self._existing_local_snapshot(local_snapshot)
        return d

    @inline_callbacks
    def when_idle(self):
        """
        Wait until we are in an 'idle' state (up_to_date or conflicted or
        failed).
        """
        if self._is_working is None:
            return
        yield self._is_working
        return

    # all below methods are state-machine methods
    #
    # use "automat-visualize magic_folder" to get a nice diagram of
    # the state-machine which should make it easier to read through
    # this code.

    @_machine.state(initial=True)
    def _up_to_date(self):
        """
        This file is up-to-date (our local state matches all remotes and
        our Personal DMD matches our local state.
        """

    @_machine.state()
    def _downloading(self):
        """
        We are retrieving a remote update
        """

    @_machine.state()
    def _download_checking_ancestor(self):
        """
        We've found a remote update; check its parentage
        """

    @_machine.state()
    def _download_checking_local(self):
        """
        We're about to make local changes; make sure a filesystem change
        didn't sneak in.
        """

    @_machine.state()
    def _creating_snapshot(self):
        """
        We are creating a LocalSnapshot for a given update
        """

    @_machine.state()
    def _uploading(self):
        """
        We are uploading a LocalSnapshot
        """

    @_machine.state()
    def _checking_for_local_work(self):
        """
        Examining our queue of work for uploads
        """

    @_machine.state()
    def _checking_for_remote_work(self):
        """
        Examining ou queue for work for downloads
        """

    @_machine.state()
    def _updating_personal_dmd_upload(self):
        """
        We are updating Tahoe state after an upload
        """

    @_machine.state()
    def _updating_personal_dmd_download(self):
        """
        We are updating Tahoe state after a download
        """

    @_machine.state()
    def _conflicted(self):
        """
        There is a conflit that must be resolved
        """

    @_machine.state()
    def _failed(self):
        """
        Something has gone completely wrong.
        """

    @_machine.input()
    def _local_update(self):
        """
        The file is changed locally (created or updated or deleted)
        """

    @_machine.input()
    def _queued_upload(self, snapshot):
        """
        We finished one upload, but there is more
        """

    @_machine.input()
    def _no_upload_work(self, snapshot):
        """
        We finished one upload and there is no more
        """

    @_machine.input()
    def _download_mismatch(self, snapshot, staged_path):
        """
        The local file does not match what we expect given database state
        """

    @_machine.input()
    def _download_matches(self, snapshot, staged_path, local_pathstate):
        """
        The local file (if any) matches what we expect given database
        state
        """

    @_machine.input()
    def _remote_update(self, snapshot):
        """
        The file has a remote update.

        XXX should this be 'snapshots' for multiple participant updates 'at once'?
        XXX does this include deletes?
        """

    @_machine.input()
    def _existing_local_snapshot(self, snapshot):
        """
        One or more LocalSnapshot instances already exist for this path.

        :param LocalSnapshot snapshot: the snapshot (which should be
            the 'youngest' if multiple linked snapshots exist).
        """

    @_machine.input()
    def _existing_conflict(self):
        """
        This path is already conflicted. Used when initializing a fresh
        file from the database.
        """

    @_machine.input()
    def _download_completed(self, snapshot, staged_path):
        """
        A remote Snapshot has been downloaded
        """

    @_machine.input()
    def _snapshot_completed(self, snapshot):
        """
        A LocalSnapshot for this update is created
        """

    @_machine.input()
    def _upload_completed(self, snapshot):
        """
        A LocalSnapshot has been turned into a RemoteSnapshot
        """

    @_machine.input()
    def _personal_dmd_updated(self, snapshot):
        """
        An update to our Personal DMD has been completed
        """

    @_machine.input()
    def _fatal_error_download(self, snapshot):
        """
        An error has occurred with no other recovery path.
        """

    @_machine.input()
    def _queued_download(self, snapshot):
        """
        There is queued RemoteSnapshot work
        """

    @_machine.input()
    def _no_download_work(self, snapshot):
        """
        There is no queued RemoteSnapshot work
        """

    @_machine.input()
    def _conflict_resolution(self, snapshot):
        """
        A conflicted file has been resolved
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

    @_machine.input()
    def _ancestor_we_are_newer(self, snapshot, staged_path):
        """
        The proposed update is _our_ ancestor.
        """

    @_machine.input()
    def _cancel(self, snapshot):
        """
        We have been cancelled
        """

    @_machine.output()
    def _begin_download(self, snapshot):
        """
        Download a given Snapshot (including its content)
        """

        def downloaded(staged_path):
            self._call_later(self._download_completed, snapshot, staged_path)

        retry_delay_sequence = _delay_sequence()

        def error(f):
            if f.check(CancelledError):
                self._factory._folder_status.error_occurred(
                    "Cancelled: {}".format(self._relpath)
                )
                self._call_later(self._cancel, snapshot)
                return

            self._factory._folder_status.error_occurred(
                "Failed to download snapshot for '{}'.".format(
                    self._relpath,
                )
            )
            with self._action.context():
                write_failure(f)
            delay_amt = next(retry_delay_sequence)
            delay = self._delay_later(delay_amt, perform_download)
            delay.addErrback(error)
            return None

        @inline_callbacks
        def perform_download():
            if snapshot.content_cap is None:
                d = succeed(None)
            else:
                d = self._factory._download_parallel.acquire()

                def work(ignore):
                    self._factory._folder_status.download_started(self._relpath)
                    return maybeDeferred(
                        self._factory._magic_fs.download_content_to_staging,
                        snapshot.relpath,
                        snapshot.content_cap,
                        self._factory._tahoe_client,
                    )
                d.addCallback(work)

                def clean(arg):
                    self._factory._download_parallel.release()
                    return arg
                d.addBoth(clean)

            d.addCallback(downloaded)
            d.addErrback(error)
            return d

        return perform_download()

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
        try:
            current_pathstate = self._factory._config.get_currentsnapshot_pathstate(self._relpath)
        except KeyError:
            current_pathstate = None

        # we give this downstream to the mark-overwrite, ultimately,
        # so it can double-check that there was no last-millisecond
        # change to the local path (note local_pathinfo.state will be
        # None if there is no file at all here)
        local_pathinfo = get_pathinfo(self._path)

        # if we got a local-update during the "download" branch, we
        # will have queued it .. but it should be impossible for a
        # snapshot to be in our local database
        try:
            self._factory._config.get_local_snapshot(self._relpath)
            assert False, "unexpected local snapshot; state-machine inconsistency?"
        except KeyError:
            pass

        # now, determine if we've found a local update
        if current_pathstate is None:
            if local_pathinfo.exists:
                self._call_later(self._download_mismatch, snapshot, staged_path)
                return
        else:
            # we've seen this file before so its pathstate should
            # match what we expect according to the database .. or
            # else some update happened meantime.
            if current_pathstate != local_pathinfo.state:
                self._call_later(self._download_mismatch, snapshot, staged_path)
                return

        self._call_later(self._download_matches, snapshot, staged_path, local_pathinfo.state)

    @_machine.output()
    def _check_ancestor(self, snapshot, staged_path):
        """
        Check if the ancestor for this remote update is correct or not.
        """
        try:
            remote_cap = self._factory._config.get_remotesnapshot(snapshot.relpath)
        except KeyError:
            remote_cap = None

        # if remote_cap is None, we've never seen this before (so the
        # ancestor is always correct)
        if remote_cap is not None:
            ancestor = self._factory._remote_cache.is_ancestor_of(remote_cap, snapshot.capability)
            if not ancestor:
                # if the incoming remotesnapshot is actually an
                # ancestor of _our_ snapshot, then we have nothing to
                # do because we are newer
                if self._factory._remote_cache.is_ancestor_of(snapshot.capability, remote_cap):
                    self._call_later(self._ancestor_we_are_newer, snapshot, staged_path)
                    return
                Message.log(
                    message_type="ancestor_mismatch",
                )
                self._call_later(self._ancestor_mismatch, snapshot, staged_path)
                return
        self._call_later(self._ancestor_matches, snapshot, staged_path)
        return

    @_machine.output()
    def _perform_remote_update(self, snapshot, staged_path, local_pathstate):
        """
        Resolve a remote update locally

        :param PathState local_pathstate: the PathState of the local
            file as it existed _right_ before we concluded it was fine
            (None if there was no local file before now)
        """
        # between when we checked for a local conflict while in the
        # _download_checking_local and when we _actually_ overwrite
        # the file (inside .mark_overwrite) there is an additional
        # window for last-second changes to happen .. we do the
        # equivalent of the dance described in detail in
        # https://magic-folder.readthedocs.io/en/latest/proposed/magic-folder/remote-to-local-sync.html#earth-dragons-collisions-between-local-filesystem-operations-and-downloads
        # although that spec doesn't include when to remove the
        # ".backup" files -- we use local_pathstate to double-check
        # that.

        if snapshot.content_cap is None:
            self._factory._magic_fs.mark_delete(snapshot.relpath)
            path_state = None
        else:
            try:
                path_state = self._factory._magic_fs.mark_overwrite(
                    snapshot.relpath,
                    snapshot.metadata["modification_time"],
                    staged_path,
                    local_pathstate,
                )
            except OSError as e:
                self._factory._folder_status.error_occurred(
                    "Failed to overwrite file '{}': {}".format(snapshot.relpath, str(e))
                )
                with self._action.context():
                    write_traceback()
                self._call_later(self._fatal_error_download, snapshot)
                return
            except BackupRetainedError as e:
                # this means that the mark_overwrite() code has
                # noticed some mismatch to the replaced file or its
                # .snaptmp version -- so this is a conflict, but we
                # didn't detect it in the _download_check_local since
                # it happened in the window _after_ that check.
                self._factory._folder_status.error_occurred(
                    "Unexpected content in '{}': {}".format(snapshot.relpath, str(e))
                )
                with self._action.context():
                    write_traceback()
                # mark as a conflict -- we use the retained tmpfile as
                # the original "staged" path here, causing "our"
                # emergency data to be in the conflict file .. maybe
                # this should just be the original tmpfile and we
                # shouldn't mess with it further?
                self._call_later(self._download_mismatch, snapshot, e.path)
                return

        # Note, if we crash here (after moving the file into place but
        # before noting that in our database) then we could produce
        # LocalSnapshots referencing the wrong parent. We will no
        # longer produce snapshots with the wrong parent once we
        # re-run and get past this point.

        # remember the last remote we've downloaded
        self._factory._config.store_downloaded_snapshot(
            snapshot.relpath, snapshot, path_state
        )

        def updated_snapshot(arg):
            self._factory._config.store_currentsnapshot_state(
                snapshot.relpath,
                path_state,
            )
            self._call_later(self._personal_dmd_updated, snapshot)
            return

        retry_delay_sequence = _delay_sequence()

        # It probably makes sense to have a separate state for this
        # part ("update remote dmd"). If we crash here (e.g. Tahoe is
        # down, keep retrying, but subsequently crash) and then
        # restart, we just won't update the remote DMD. So "something"
        # should notice at startup that 'store_downloaded_snapshot'
        # has run but not this part (because the database has a
        # different entry than the remote DMD) and inject an event to
        # get us here.

        def error(f):
            # XXX really need to "more visibly" log things like syntax
            # errors etc...
            write_failure(f)
            if f.check(CancelledError):
                self._factory._folder_status.error_occurred(
                    "Cancelled: {}".format(self._relpath)
                )
                self._call_later(self._cancel, snapshot)
                return

            self._factory._folder_status.error_occurred(
                "Error updating personal DMD: {}".format(f.getErrorMessage())
            )
            with self._action.context():
                write_failure(f)
            delay_amt = next(retry_delay_sequence)
            delay = self._delay_later(delay_amt, perform_update)
            delay.addErrback(error)
            return None

        def perform_update():
            d = maybeDeferred(
                self._factory._write_participant.update_snapshot,
                snapshot.relpath,
                snapshot.capability,
            )
            d.addCallback(updated_snapshot)
            d.addErrback(error)
            return d

        d = perform_update()
        return d

    @_machine.output()
    def _status_upload_queued(self):
        self._factory._folder_status.upload_queued(self._relpath)

    # the uploader does this status because only it knows when the
    # item is out of the queue and "actually" starts uploading..
    # @_machine.output()
    # def _status_upload_started(self):
    #     self._factory._folder_status.upload_started(self._relpath)

    @_machine.output()
    def _status_upload_finished(self):
        self._factory._folder_status.upload_finished(self._relpath)

    @_machine.output()
    def _status_download_queued(self):
        self._factory._folder_status.download_queued(self._relpath)

    @_machine.output()
    def _status_download_finished(self):
        self._factory._folder_status.download_finished(self._relpath)

    @_machine.output()
    def _cancel_queued_work(self):
        for d in self._queue_local:
            d.cancel()
        for d in self._queue_remote:
            d.cancel()

    @_machine.output()
    def _create_local_snapshot(self):
        """
        Create a LocalSnapshot for this update
        """
        d = self._factory._local_snapshot_service.add_file(self._path)

        # when the local snapshot gets created, it _should_ have the
        # next thing in our queue (if any) as its parent (see assert below)

        def completed(snap):
            # _queue_local contains Deferreds .. but ideally we'd
            # check if "the thing those deferreds resolves to" is the
            # right one .. namely, the _next_ thing in the queue
            # should be (one of) "snap"'s parents
            self._call_later(self._snapshot_completed, snap)
            return snap

        d.addCallback(completed)
        # errback? (re-try?)  XXX probably have to have a 'failed'
        # state? or might re-trying work eventually? (I guess
        # .. maybe? Like if the disk is currently too full to copy but
        # it might eventually get less-full?)
        return d

    @_machine.output()
    def _begin_upload(self, snapshot):
        """
        Begin uploading a LocalSnapshot (to create a RemoteSnapshot)
        """
        assert snapshot is not None, "Internal inconsistency: no snapshot _begin_upload"
        with self._action.context():
            d = self._factory._uploader.upload_snapshot(snapshot)

            retry_delay_sequence = _delay_sequence()

            def upload_error(f, snap):
                write_failure(f)
                if f.check(CancelledError):
                    self._factory._folder_status.error_occurred(
                        "Cancelled: {}".format(self._relpath)
                    )
                    self._call_later(self._cancel, snapshot)
                    return
                if f.check(ResponseNeverReceived):
                    for reason in f.value.reasons:
                        if reason.check(CancelledError):
                            self._factory._folder_status.error_occurred(
                                "Cancelled: {}".format(self._relpath)
                            )
                            self._call_later(self._cancel, snapshot)
                            return

                # upon errors, we wait a little and then retry,
                # putting the item back in the uploader queue
                self._factory._folder_status.error_occurred(
                    "Error uploading {}: {}".format(self._relpath, f.getErrorMessage())
                )
                delay_amt = next(retry_delay_sequence)
                delay = self._delay_later(delay_amt, self._factory._uploader.upload_snapshot, snap)
                delay.addCallback(got_remote)
                delay.addErrback(upload_error, snap)
                return delay

            def got_remote(remote):
                # successfully uploaded
                snapshot.remote_snapshot = remote
                self._factory._remote_cache._cached_snapshots[remote.capability.danger_real_capability_string()] = remote
                self._call_later(self._upload_completed, snapshot)

            d.addCallback(got_remote)
            d.addErrback(upload_error, snapshot)
            return d

    @_machine.output()
    def _mark_download_conflict(self, snapshot, staged_path):
        """
        Mark a conflict for this remote snapshot
        """
        conflict_path = "{}.conflict-{}".format(
            self._relpath,
            snapshot.author.name
        )
        self._factory._magic_fs.mark_conflict(self._relpath, conflict_path, staged_path)
        self._factory._config.add_conflict(snapshot)

    @_machine.output()
    def _update_personal_dmd_upload(self, snapshot):
        """
        Update our personal DMD (after an upload)
        """

        retry_delay_sequence = _delay_sequence()

        def error(f):
            write_failure(f)
            if f.check(CancelledError):
                self._factory._folder_status.error_occurred(
                    "Cancelled: {}".format(self._relpath)
                )
                self._call_later(self._cancel, snapshot)
                return

            self._factory._folder_status.error_occurred(
                "Error updating personal DMD: {}".format(f.getErrorMessage())
            )
            with self._action.context():
                write_failure(f)
            delay_amt = next(retry_delay_sequence)
            delay = self._delay_later(delay_amt, update_personal_dmd)
            delay.addErrback(error)
            return None

        @inline_callbacks
        def update_personal_dmd():
            remote_snapshot = snapshot.remote_snapshot
            assert remote_snapshot is not None, "remote-snapshot must exist"
            # update the entry in the DMD
            yield self._factory._write_participant.update_snapshot(
                snapshot.relpath,
                remote_snapshot.capability,
            )

            # if removing the stashed content fails here, we MUST move
            # on to delete the LocalSnapshot because we may not be
            # able to re-create the Snapshot (e.g. maybe the stashed
            # content is "partially deleted" or otherwise unreadable)
            # and we _don't_ want to deserialize the LocalSnapshot if
            # the process restarts
            if snapshot.content_path is not None:
                try:
                    # Remove the local snapshot content from the stash area.
                    snapshot.content_path.remove()
                except Exception as e:
                    self._factory._folder_status.error_occurred(
                        "Failed to remove cache file '{}': {}".format(
                            snapshot.content_path.path,
                            str(e),
                        )
                    )

            # Remove the LocalSnapshot from the db.
            yield self._factory._config.delete_local_snapshot(snapshot, remote_snapshot)

            # Signal ourselves asynchronously so that the machine may
            # finish this output (and possibly more) before dealing
            # with this new input
            self._call_later(self._personal_dmd_updated, snapshot)
        d = update_personal_dmd()
        d.addErrback(error)

    @_machine.output()
    def _queue_local_update(self):
        """
        Save this update for later processing (in _check_for_local_work)
        """
        d = self._factory._local_snapshot_service.add_file(self._path)
        self._queue_local.append(d)

        # ideally, we'd double-check the semantics of this snapshot
        # when it is created: it should have as parents anything else
        # in the queue -- but of course, we can't check until _those_
        # snapshots are themselves created.

        # XXX what do we do if this snapshot fails / errback()s? go to
        # "failed" state?

        # return a "fresh" Deferred, because callers can't be trusted
        # not to mess with our return-value
        ret_d = Deferred()

        def failed(f):
            # this still works for CancelledError right?
            ret_d.errback(f)

        def got_snap(snap):
            ret_d.callback(snap)
            return snap
        d.addCallbacks(got_snap, failed)
        return ret_d

    @_machine.output()
    def _queue_remote_update(self, snapshot):
        """
        Save this remote snapshot for later processing (in _check_for_remote_work)
        """
        d = Deferred()
        self._queue_remote.append((d, snapshot))
        return d

    @_machine.output()
    def _check_for_local_work(self):
        """
        Inject any queued local updates
        """
        if self._queue_local:
            snapshot_d = self._queue_local.pop(0)

            def got_snapshot(snap):
                self._call_later(self._snapshot_completed, snap)
                return snap
            snapshot_d.addCallback(got_snapshot)
            return
        self._call_later(self._no_upload_work, None)

    @_machine.output()
    def _check_for_remote_work(self):
        """
        Inject any saved remote updates.
        """
        if self._queue_remote:
            d, snapshot = self._queue_remote.pop(0)

            def do_remote_update(done_d, snap):
                update_d = self._queued_download(snap)
                update_d.addBoth(done_d.callback)
            self._call_later(do_remote_update, d, snapshot)
            return
        self._call_later(self._no_download_work, None)

    @_machine.output()
    def _working(self):
        """
        We are doing some work so this file is not currently updated.
        """
        assert self._is_working is None, "Internal inconsistency"
        self._is_working = Deferred()

    @_machine.output()
    def _done_working(self):
        """
        Alert any listeners awaiting for this file to become idle (note
        that 'confliced' is also an idle state)
        """
        assert self._is_working is not None, "Internal inconsistency"
        d = self._is_working
        self._is_working = None
        d.callback(None)

    _up_to_date.upon(
        _remote_update,
        enter=_downloading,
        outputs=[_working, _status_download_queued, _begin_download],
        collector=_last_one,
    )

    _download_checking_ancestor.upon(
        _remote_update,
        enter=_download_checking_ancestor,
        outputs=[_status_download_queued, _queue_remote_update],
        collector=_last_one,
    )

    _download_checking_ancestor.upon(
        _ancestor_mismatch,
        enter=_conflicted,
        outputs=[_mark_download_conflict, _status_download_finished, _done_working],
        collector=_last_one,
    )
    _download_checking_ancestor.upon(
        _ancestor_matches,
        enter=_download_checking_local,
        outputs=[_check_local_update],
        collector=_last_one,
    )
    _download_checking_ancestor.upon(
        _ancestor_we_are_newer,
        enter=_up_to_date,
        outputs=[_status_download_finished, _done_working],
        collector=_last_one,
    )

    _downloading.upon(
        _download_completed,
        enter=_download_checking_ancestor,
        outputs=[_check_ancestor],
        collector=_last_one,
    )
    _downloading.upon(
        _remote_update,
        enter=_downloading,
        outputs=[_status_download_queued, _queue_remote_update],
        collector=_last_one,
    )
    _downloading.upon(
        _cancel,
        enter=_failed,
        outputs=[_cancel_queued_work, _status_download_finished, _done_working],
        collector=_last_one,
    )

    _download_checking_local.upon(
        _download_matches,
        enter=_updating_personal_dmd_download,
        outputs=[_perform_remote_update],
        collector=_last_one,
    )
    _download_checking_local.upon(
        _download_mismatch,
        enter=_conflicted,
        outputs=[_mark_download_conflict, _status_download_finished, _done_working],
        collector=_last_one,
    )

    _up_to_date.upon(
        _local_update,
        enter=_creating_snapshot,
        outputs=[_working, _status_upload_queued, _create_local_snapshot],
        collector=_last_one,
    )
    _up_to_date.upon(
        _existing_conflict,
        enter=_conflicted,
        outputs=[],  # up_to_date and conflicted are both "idle" states
    )
    _up_to_date.upon(
        _existing_local_snapshot,
        enter=_uploading,
        outputs=[_working, _status_upload_queued, _begin_upload],
        collector=_last_one,
    )
    _creating_snapshot.upon(
        _snapshot_completed,
        enter=_uploading,
        outputs=[_begin_upload],
        collector=_last_one,
    )

    # XXX actually .. we should maybe re-start this snapshot? This
    # means we've found a change _while_ we're trying to create the
    # snapshot .. what if it's like half-created?
    # (hmm, should be impossible if we wait for the snapshot in the
    # scanner...but window for the API to hit it still ..)
    _creating_snapshot.upon(
        _local_update,
        enter=_creating_snapshot,
        outputs=[_queue_local_update],
        collector=_last_one,
    )
    _uploading.upon(
        _upload_completed,
        enter=_updating_personal_dmd_upload,
        outputs=[_update_personal_dmd_upload],
        collector=_last_one,
    )
    _uploading.upon(
        _local_update,
        enter=_uploading,
        outputs=[_queue_local_update],
        collector=_last_one,
    )
    _uploading.upon(
        _cancel,
        enter=_failed,
        outputs=[_cancel_queued_work, _status_upload_finished, _done_working],
        collector=_last_one,
    )

    # there is async-work done by _update_personal_dmd_upload, after
    # which personal_dmd_updated is input back to the machine
    _updating_personal_dmd_upload.upon(
        _personal_dmd_updated,
        enter=_checking_for_local_work,
        outputs=[_status_upload_finished, _check_for_local_work],
        collector=_last_one,
    )
    _updating_personal_dmd_upload.upon(
        _cancel,
        enter=_failed,
        outputs=[_cancel_queued_work, _status_upload_finished, _done_working],
        collector=_last_one,
    )

    # downloader updates
    _updating_personal_dmd_download.upon(
        _personal_dmd_updated,
        enter=_checking_for_local_work,
        outputs=[_status_download_finished, _check_for_local_work],
        collector=_last_one,
    )
    _updating_personal_dmd_download.upon(
        _cancel,
        enter=_failed,
        outputs=[_cancel_queued_work, _status_download_finished, _done_working],
        collector=_last_one,
    )
    _updating_personal_dmd_download.upon(
        _fatal_error_download,
        enter=_failed,
        outputs=[_status_download_finished, _done_working],
        collector=_last_one,
    )
    # this is the "last-minute" conflict window -- that is, when
    # .mark_overwrite() determines something wrote to the tempfile (or
    # wrote to the "real" file immediately after the state-machine
    # check)
    _updating_personal_dmd_download.upon(
        _download_mismatch,
        enter=_conflicted,
        outputs=[_mark_download_conflict, _status_download_finished, _done_working],
        collector=_last_one,
    )

    _checking_for_local_work.upon(
        _snapshot_completed,
        enter=_uploading,
        outputs=[_status_upload_queued, _begin_upload],
        collector=_last_one,
    )
    _checking_for_local_work.upon(
        _no_upload_work,
        enter=_checking_for_remote_work,
        outputs=[_check_for_remote_work],
        collector=_last_one,
    )
    _checking_for_local_work.upon(
        _remote_update,
        enter=_checking_for_local_work,
        outputs=[_queue_remote_update],
        collector=_last_one,
    )

    _checking_for_remote_work.upon(
        _queued_download,
        enter=_downloading,
        outputs=[_begin_download],
        collector=_last_one,
    )
    _checking_for_remote_work.upon(
        _no_download_work,
        enter=_up_to_date,
        outputs=[_done_working],
        collector=_last_one,
    )
    _checking_for_remote_work.upon(
        _local_update,
        enter=_checking_for_remote_work,
        outputs=[_queue_local_update],
        collector=_last_one,
    )

    # if we get a remote-update while we're in
    # "updating_personal_dmd_upload" we will enter _conflicted, then
    # the DMD will be updated (good), and we get the
    # "personal_dmd_updated" notification ..
    _conflicted.upon(
        _personal_dmd_updated,
        enter=_conflicted,
        outputs=[_status_upload_finished],
        collector=_last_one,
    )

    # in these transitions we queue them (instead of going straight to
    # 'conflicted') so we download the content, which is required for
    # the subsequent conflict-file (which we'll hit because the
    # snapshot we're creating can't have the right parent yet)
    _creating_snapshot.upon(
        _remote_update,
        enter=_creating_snapshot,
        outputs=[_status_download_queued, _queue_remote_update],
        collector=_last_one,
    )
    _uploading.upon(
        _remote_update,
        enter=_uploading,
        outputs=[_status_download_queued, _queue_remote_update],
        collector=_last_one,
    )
    _downloading.upon(
        _local_update,
        enter=_downloading,
        outputs=[_queue_local_update],
        collector=_last_one,
    )
    _updating_personal_dmd_download.upon(
        _remote_update,
        enter=_updating_personal_dmd_download,
        outputs=[_status_download_queued, _queue_remote_update],
        collector=_last_one,
    )
    _updating_personal_dmd_download.upon(
        _local_update,
        enter=_updating_personal_dmd_download,
        outputs=[_queue_local_update],  # XXX or, can/should we go straight to conflicted?
        collector=_last_one,
    )

    _updating_personal_dmd_upload.upon(
        _remote_update,
        enter=_updating_personal_dmd_upload,
        outputs=[_status_download_queued, _queue_remote_update],
        collector=_last_one,
    )
    _updating_personal_dmd_upload.upon(
        _local_update,
        enter=_updating_personal_dmd_upload,
        outputs=[_queue_local_update],
        collector=_last_one,
    )

    _conflicted.upon(
        _conflict_resolution,
        enter=_uploading,
        outputs=[_begin_upload],
        collector=_last_one,
    )
    _conflicted.upon(
        _remote_update,
        enter=_conflicted,
        outputs=[],  # probably want to .. do something? remember it?
    )
    _conflicted.upon(
        _local_update,
        enter=_conflicted,
        outputs=[],  # nothing, likely: user messing with resolution file?
    )

    _failed.upon(
        _local_update,
        enter=_failed,
        outputs=[],  # should perhaps record (another) error?
    )
    _failed.upon(
        _remote_update,
        enter=_failed,
        outputs=[],  # should perhaps record (another) error?
    )
