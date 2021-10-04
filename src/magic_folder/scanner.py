# Copyright (c) Least Authority TFA GmbH.

"""
Scan a Magic Folder for changes.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import attr
from eliot import current_action, start_action, write_failure
from eliot.twisted import inline_callbacks
from twisted.application.internet import TimerService
from twisted.application.service import MultiService
from twisted.internet.defer import DeferredLock, gatherResults
from twisted.internet.task import Cooperator

from .util.file import get_pathinfo
from .util.twisted import exclusively


def _create_cooperator(clock):
    """
    Create a cooperator
    """

    def schedule(f):
        return clock.callLater(0, f)

    # NOTE: We don't use CooperatorSevice here, since:
    # - There is not a way to set the reactor it uses
    # - We want to wait to stop the cooperator until after the TimerService for
    #   periodic scans has stopped, so that the Cooperator will have no pending
    #   work.
    return Cooperator(
        scheduler=schedule,
    )


@attr.s
class ScannerService(MultiService):
    """
    Periodically scan a local Magic Folder for new or updated files, and request
    the local snapshot service snapshot them.
    """

    _config = attr.ib()
    _local_snapshot_service = attr.ib()
    _status = attr.ib()
    _cooperator = attr.ib()
    _scan_interval = attr.ib()
    _lock = attr.ib(init=False, factory=DeferredLock)

    @classmethod
    def from_config(cls, clock, folder_config, local_snapshot_service, status):
        return cls(
            config=folder_config,
            local_snapshot_service=local_snapshot_service,
            status=status,
            cooperator=_create_cooperator(clock),
            scan_interval=folder_config.scan_interval,
        )

    def __attrs_post_init__(self):
        super(ScannerService, self).__init__()
        if self._scan_interval is not None:
            TimerService(
                self._scan_interval,
                self._loop,
            ).setServiceParent(self)

    def stopService(self):
        return (
            super(ScannerService, self)
            .stopService()
            .addCallback(lambda _: self._cooperator.stop())
        )

    def scan_once(self):
        """
        Perform a scan for new files.
        """
        return self._scan()

    def _loop(self):
        """
        Called periodically to scan for new files.

        Performs a scan for files, and logs and consumes all errors.
        """
        return self._scan().addErrback(write_failure)

    @exclusively
    @inline_callbacks
    def _scan(self):
        """
        Perform a scan for new files, and wait for all the snapshots to be
        complete.
        """
        # TODO: Do we always want to wait for all the files to be snapshotted?
        # If we don't wait for snapshotting to complete, then we probably want
        # to coalesce multiple outstanding snapshot requests for the same file,
        # or otherwise ensure that we don't end up snapshotting an unchanged
        # file.
        results = []

        def process(path):
            d = self._local_snapshot_service.add_file(path)
            d.addErrback(write_failure)
            results.append(d)

        with start_action(action_type="scanner:find-updates"):
            yield find_updated_files(
                self._cooperator, self._config, process, self._status
            )
            yield find_deleted_files(
                self._cooperator, self._config, process, self._status
            )
            yield gatherResults(results)
        # XXX update/use IStatus to report scan start/end


def find_updated_files(cooperator, folder_config, on_new_file, status):
    """
    :param Cooperator cooperator: The cooperator to use to control yielding to
        the reactor.
    :param MagicFolderConfig folder_config: the folder for which we
        are scanning

    :param Callable[[FilePath], None] on_new_file:
        This function will be invoked for each updated / new file we find. The
        argument will be a FilePath of the updated/new file.

    :param FileStatus status: The status implementation to report errors to.

    :returns Deferred[None]: Deferred that fires once the scan is complete.
    """
    action = current_action()
    magic_path = folder_config.magic_path
    bytes_path = magic_path.asBytesMode("utf-8")

    # XXX we don't handle deletes
    def process_file(path):
        with action.context():
            relpath = "/".join(path.segmentsFrom(magic_path))
            with start_action(action_type="scanner:find-updates:file", relpath=relpath):
                # NOTE: Make sure that we get both these states without yielding
                # to the reactor. Otherwise, we may detect a changed made by us
                # as a new change.
                path_info = get_pathinfo(path)
                try:
                    snapshot_state = folder_config.get_currentsnapshot_pathstate(
                        relpath
                    )
                except KeyError:
                    snapshot_state = None

                if not path_info.is_file:
                    if snapshot_state is not None:
                        status.error_occurred(
                            "File {} was a file, and now is {}.".format(
                                relpath, "a directory" if path_info.is_dir else "not"
                            )
                        )
                    action.add_success_fields(changed_type=True)
                    return
                if path_info.state != snapshot_state:
                    # TODO: We may also want to compare checksums here,
                    # to avoid `touch(1)` creating a new snapshot.
                    action.add_success_fields(update=True)
                    on_new_file(path)
                else:
                    action.add_success_fields(update=True)

    return cooperator.coiterate(
        (
            process_file(path.asTextMode("utf-8"))
            for path in bytes_path.walk()
            if path != bytes_path
        )
    )


def find_deleted_files(cooperator, folder_config, on_deleted_file, status):
    """
    :param Cooperator cooperator: The cooperator to use to control yielding to
        the reactor.
    :param MagicFolderConfig folder_config: the folder for which we
        are scanning

    :param Callable[[FilePath], None] on_deleted_file:
        This function will be invoked for each deleted file we find. The
        argument will be a FilePath of the deleted file.

    :param FileStatus status: The status implementation to report errors to.

    :returns Deferred[None]: Deferred that fires once the scan is complete.
    """

    def process_file(relpath):
        """
        Check if this file still exists locally; if not, it's a delete
        """
        path = folder_config.magic_path.preauthChild(relpath)
        if not path.asBytesMode("utf8").exists():
            try:
                local = folder_config.get_local_snapshot(relpath)
            except KeyError:
                local = None
            try:
                _, remote_content, _ = folder_config.get_remotesnapshot_caps(relpath)
            except KeyError:
                remote_content = False

            if local is None or not local.is_delete():
                if remote_content is not None:
                    on_deleted_file(path)

    return cooperator.coiterate(
        (
            process_file(relpath)
            for relpath in folder_config.get_all_snapshot_paths()
        )
    )
