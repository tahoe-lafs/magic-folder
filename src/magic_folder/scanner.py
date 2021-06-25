from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import attr
from zope.interface import (
    implementer,
)
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.internet.task import (
    deferLater,
)
from twisted.application.internet import (
    TimerService,
)
from twisted.application.service import (
    IService,
)
from eliot import (
    start_action,
)

from .magicpath import (
    path2magic,
)


@attr.s
@implementer(IService)
class ScannerService(TimerService):
    """
    Periodically scan a local Magic Folder for new or updated files
    """
    _reactor = attr.ib()
    _config = attr.ib()
    _local_snapshot_service = attr.ib()
    _status = attr.ib()

    def __attrs_post_init__(self):
        assert self._config.scan_interval > 0, "Illegal scan_interval"
        TimerService.__init__(
            self,
            self._config.scan_interval,
            self._scan,
        )

    @inlineCallbacks
    def _scan(self):
        """
        Perform a scan for new files.
        """
        # XXX probably want a lock ("or something") so we don't do
        # overlapping scans (i.e. if a scan takes longer than the
        # scan_interval we should not start a second one)
        with start_action(action_type="scanner:find-updates") as action:
            duration = yield find_updated_files(self._reactor, self._config, self._modified_file)
            action.add_success_fields(scan_duration=duration)
        # XXX update/use IStatus to report scan start/end

    def _modified_file(self, path):
        """
        Internal helper.
        Called when we find a new or modified file.
        """
        d = self._local_snapshot_service.add_file(path)

        def bad(f):
            # might want to expose some errors to users / status
            print(f)
        d.addErrback(bad)


def _is_newer_than_current(folder_config, name, local_mtime):
    """
    Determine if `local_mtime` is newer than the existing Snapshot for
    `name`. If there is no existing Snapshot for the name then True is
    returned.

    :param folder_config: our configuration
    :param unicode name: the mangled name of the Snapshot
    :param int local_mtime: timestamp of the current local file, in seconds.
    """
    # if we have a LocalSnapshot we've already queued up some changes
    try:
        localsnap = folder_config.get_local_snapshot(name)
        existing_mtime = localsnap.metadata["mtime"]
    except KeyError:
        existing_mtime = None

    # if we have no LocalSnapshot(s) proceed to see if we have a
    # remotesnapshot recorded
    if existing_mtime is None:
        try:
            existing_mtime = folder_config.get_remotesnapshot_mtime(name)
        except KeyError:
            existing_mtime = None

    if existing_mtime is None:
        # we have no record of this file; it must be new.
        return True
    return local_mtime != existing_mtime


@inlineCallbacks
def find_updated_files(reactor, folder_config, on_new_file, _yield_interval=0.100):
    """
    :param IReactor reactor: our reactor and source of time

    :param MagicFolderConfig folder_config: the folder for which we
        are scanning

    :param callable on_new_file: a 1-argument callable. This function
        will be invoked for each updated / new file we find. The
        argument will be a FilePath of the updated/new file.

    :param float _yield_interval: how often to return control to the
        reactor in seconds

    :returns: the scan duration, in seconds
    """

    started = last_yield = reactor.seconds()

    # XXX we don't handle deletes

    for path in folder_config.magic_path.walk():
        if path.isdir():
            continue
        relpath = "/".join(path.segmentsFrom(folder_config.magic_path))
        name = path2magic(relpath)
        if _is_newer_than_current(folder_config, name, int(path.getModificationTime())):
            on_new_file(path)

        if reactor.seconds() - last_yield > _yield_interval:
            yield deferLater(reactor, 0.0, lambda: None)
            last_yield = reactor.seconds()
    duration = reactor.seconds() - started
    returnValue(duration)
