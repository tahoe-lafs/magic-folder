# Copyright 2022 Least Authority TFA GmbH
# See COPYING for details.

import os
import psutil
from contextlib import (
    contextmanager,
)


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
    # check if we have another instance running already
    if pidfile.exists():
        with pidfile.open("r") as f:
            content = f.read().decode("utf8").strip()
        pid, starttime = content.split()
        pid = int(pid)
        starttime = float(starttime)
        try:
            # if any other process is running at that PID, let the
            # user decide if this is another magic-older
            # instance. Automated programs may use the start-time to
            # help decide this (if the PID is merely recycled, the
            # start-time won't match).
            proc = find_process(pid)
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

    yield  # setup completed, await cleanup

    log.debug("Removing {pidpath}", pidpath=pidfile.path)
    try:
        pidfile.remove()
    except Exception as e:
        log.error(
            "Couldn't remove '{pidfile}': {err}.",
            pidfile=pidfile.path,
            err=e,
        )
