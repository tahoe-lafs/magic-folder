# Copyright (c) Least Authority TFA GmbH.

"""
Scan a Magic Folder for changes.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import attr
from eliot import start_action, write_failure
from eliot.twisted import inline_callbacks
from twisted.application.service import MultiService
from twisted.application.internet import TimerService
from twisted.internet.defer import DeferredLock, gatherResults
from twisted.internet.task import Cooperator

from .magicpath import path2magic
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
    # - Once we enable periodic scans, we want to wait to stop the cooperator
    #   stop it until after the TimerService for periodic scans has stoppped,
    #   so that the Cooperator will have no pending work.
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
        return super(ScannerService, self).stopService().addCallback(
            lambda _: self._cooperator.stop()
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
            yield find_updated_files(self._cooperator, self._config, process)
            yield gatherResults(results)
        # XXX update/use IStatus to report scan start/end


def find_updated_files(cooperator, folder_config, on_new_file):
    """
    :param Cooperator cooperator: The cooperator to use to control yielding to
        the reactor.
    :param MagicFolderConfig folder_config: the folder for which we
        are scanning

    :param callable on_new_file: a 1-argument callable. This function
        will be invoked for each updated / new file we find. The
        argument will be a FilePath of the updated/new file.

    :returns Deferred[None]: Deferred that fires once the scan is complete.
    """
    # XXX we don't handle deletes
    def _process():
        for path in folder_config.magic_path.asBytesMode("utf-8").walk():
            if path.isdir():
                continue
            path = path.asTextMode("utf-8")
            relpath = "/".join(path.segmentsFrom(folder_config.magic_path))
            name = path2magic(relpath)
            try:
                snapshot_state = folder_config.get_currentsnapshot_pathstate(name)
            except KeyError:
                snapshot_state = None
            path_state = get_pathinfo(path).state
            if path_state != snapshot_state:
                # TODO: We may also want to compare checksums here,
                # to avoid `touch(1)` creating a new snapshot.
                on_new_file(path)
            yield

    return cooperator.coiterate(_process())
