from __future__ import (
    absolute_import,
    division,
    print_function,
)

import attr
from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)
from twisted.internet.task import (
    deferLater,
    react,
)

from .magicpath import (
    path2magic,
)


def _is_newer_than_current(folder_config, name, local_mtime):
    """
    Determine if `local_mtime` is newer than the existing Snapshot for
    `name`. If there is no existing Snapshot for the name then True is
    returned.

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
    return local_mtime > existing_mtime


@inlineCallbacks
def find_updated_files(reactor, folder_config, on_new_file, _yield_interval=0.100):
    """
    :param IReactor reactor: our reactor and source of time

    :param MagicFolderConfig folder_config: the folder for which we
        are scanning

    :param callable on_new_file: a 1-argument callable taking a single
        FilePath instance. This function will be invoked for each updated
        / new file we find.

    :param float _yield_interval: how often to return control to the
        reactor in seconds

    :returns: the scan duration, in seconds
    """

    started = last_yield = reactor.seconds()
    yield_interval = 0.100  # back to reactor every 100ms

    # XXX we don't handle deletes

    for path in folder_config.magic_path.walk():
        if path.isdir():
            continue
        relpath = "/".join(path.segmentsFrom(folder_config.magic_path))
        name = path2magic(relpath)
        if _is_newer_than_current(folder_config, name, int(path.getModificationTime())):
            on_new_file(path)

        if reactor.seconds() - last_yield > yield_interval:
            yield deferLater(reactor, 0.0, lambda: None)
            last_yield = reactor.seconds()
    duration = reactor.seconds() - started
    returnValue(duration)


if __name__ == "__main__":
    from magic_folder.config import load_global_configuration
    cfg = load_global_configuration(FilePath("./carol"))

    @react
    @inlineCallbacks
    def main(reactor):
        yield find_updated_files(reactor, cfg.get_magic_folder("default"), lambda _: None)
