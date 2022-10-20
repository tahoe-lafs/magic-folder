# Copyright 2022 Least Authority TFA GmbH
# See COPYING for details.

import os
import psutil
from contextlib import (
    contextmanager,
)
# the docs are a little misleading, but this is either WindowsFileLock
# or UnixFileLock depending upon the platform we're currently on
from filelock import FileLock, Timeout


class ProcessInTheWay(Exception):
    """
    our pidfile points at a running process
    """


class InvalidPidFile(Exception):
    """
    our pidfile isn't well-formed
    """


class CannotRemovePidFile(Exception):
    """
    something went wrong removing the pidfile
    """


def _pidfile_to_lockpath(pidfile):
    """
    internal helper.
    :returns FilePath: a path to use for file-locking the given pidfile
    """
    return pidfile.sibling("{}.lock".format(pidfile.basename()))


def parse_pidfile(pidfile):
    """
    :param FilePath pidfile:
    :returns tuple: 2-tuple of pid, creation-time as int, float
    :raises InvalidPidFile: on error
    """
    with pidfile.open("r") as f:
        content = f.read().decode("utf8").strip()
    try:
        pid, starttime = content.split()
        pid = int(pid)
        starttime = float(starttime)
    except ValueError:
        raise InvalidPidFile(
            "found invalid PID file in {}".format(
                pidfile
            )
        )
    return pid, starttime


@contextmanager
def check_pid_process(pidfile, log, find_process=None):
    """
    If another instance appears to be running already, raise an
    exception.  Otherwise, write our PID + start time to the pidfile
    and arrange to delete it upon exit.

    :param FilePath pidfile: the file to read/write our PID from.

    :param Logger log: a way to tell the user things

    :param Callable find_process: None, or a custom way to get a
        Process objet (usually for tests)
    """
    find_process = psutil.Process if find_process is None else find_process
    lock_path = _pidfile_to_lockpath(pidfile)

    try:
        with FileLock(lock_path.path, timeout=2):
            # check if we have another instance running already
            if pidfile.exists():
                pid, starttime = parse_pidfile(pidfile)
                try:
                    # if any other process is running at that PID, let the
                    # user decide if this is another magic-older
                    # instance. Automated programs may use the start-time to
                    # help decide this (if the PID is merely recycled, the
                    # start-time won't match).
                    find_process(pid)
                    raise Exception(
                        "A process is already running as PID {}".format(pid)
                    )
                except psutil.NoSuchProcess:
                    log.info(
                        "'{pidpath}' refers to {pid} that isn't running",
                        pidpath=pidfile.path,
                        pid=pid,
                    )
                    # nothing is running at that PID so it must be a stale file
                    pidfile.remove()

            # write our PID + start-time to the pid-file
            pid = os.getpid()
            starttime = find_process(pid).create_time()
            with pidfile.open("w") as f:
                f.write("{} {}\n".format(pid, starttime).encode("utf8"))

    except Timeout:
        # this "except" matches trying to acquire the lock
        raise ProcessInTheWay(
            "Another process is still locking {}".format(pidfile.path)
        )

    yield  # setup completed, await cleanup

    log.debug("Removing {pidpath}", pidpath=pidfile.path)
    try:
        with FileLock(lock_path.path, timeout=2):
            try:
                pidfile.remove()
            except Exception as e:
                raise CannotRemovePidFile(
                    "Couldn't remove '{pidfile}': {err}.".format(
                        pidfile=pidfile.path,
                        err=e,
                    )
                )

    except Timeout:
        raise ProcessInTheWay(
            "Another process is still locking {}".format(pidfile.path)
        )
