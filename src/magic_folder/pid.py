# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

import os
import psutil
from contextlib import (
    contextmanager,
)


@contextmanager
def check_pid_process(pidfile, log):
    """
    If another instance appears to be running already, raise an
    exception.  Otherwise, write our PID to the pidfile and arrange to
    delete it upon exit.

    :param FilePath pidfile: the file to read/write our PID from.
    """
    # check if we have another instance running already
    if pidfile.exists():
        with pidfile.open("r") as f:
            pid = int(f.read().decode("utf8").strip())
        if pid in psutil.pids():
            raise Exception(
                "An existing magic-folder process is running as process {}".format(pid)
            )
        else:
            log.info(
                "'{pidpath}' refers to {pid} that isn't running\n".format(
                    pidpath=pidfile.path,
                    pid=pid,
                )
            )
            pidfile.remove()

    # write our PID to the pid-file
    pid = os.getpid()
    with pidfile.open("w") as f:
        f.write("{}\n".format(pid).encode("utf8"))

    yield  # setup completed, await cleanup

    log.debug("Removing {pidpath}", pidpath=pidfile.path)
    pidfile.remove()


