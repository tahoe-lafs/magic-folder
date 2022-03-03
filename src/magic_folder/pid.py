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
    exception.  Otherwise, write our PID to the pidfile and arrange to
    delete it upon exit.

    :param FilePath pidfile: the file to read/write our PID from.

    :param Logger log: a way to tell the user things

    :param Callable find_process: None, or a custom way to get a
        Process objet (usually for tests)
    """
    find_process = psutil.Process if find_process is None else find_process
    # check if we have another instance running already
    if pidfile.exists():
        with pidfile.open("r") as f:
            pid = int(f.read().decode("utf8").strip())
        try:
            # if this looks like another magic-folder process, kill it
            proc = find_process(pid)
            if "magic-folder" in " ".join(proc.cmdline()).lower():
                log.info("Killing {pid}", pid=pid)
                proc.terminate()

            # ...otherwise give up
            else:
                raise Exception(
                    "A process that doesn't seem to be magic-folder is running"
                    " as {} so not killing it".format(pid)
                )
        except psutil.NoSuchProcess:
            log.info(
                "'{pidpath}' refers to {pid} that isn't running",
                pidpath=pidfile.path,
                pid=pid,
            )
            pidfile.remove()

    # write our PID to the pid-file
    pid = os.getpid()
    with pidfile.open("w") as f:
        f.write("{}\n".format(pid).encode("utf8"))

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
