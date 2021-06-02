# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functionality for operating on a Tahoe-LAFS node.
"""

from __future__ import (
    absolute_import,
    division,
    print_function,
)

from sys import (
    executable,
)

from os import (
    environ,
)

from ConfigParser import (
    ConfigParser,
)

from eliot import (
    Message,
)

from magic_folder.util.eliotutil import (
    log_call_deferred,
)

from twisted.internet.defer import (
    inlineCallbacks,
)
from twisted.internet.utils import (
    getProcessOutputAndValue,
)

def tahoe_lafs_args(argv):
    return [b"-m", b"allmydata.scripts.runner"] + argv

def to_configparser(configuration):
    configparser = ConfigParser()
    for section, items in configuration.items():
        configparser.add_section(section)
        for (key, value) in items.items():
            configparser.set(section, key, value)
    return configparser

def write_configuration(node_directory, config):
    with node_directory.child(b"tahoe.cfg").open("w") as fobj:
        config.write(fobj)

@log_call_deferred(u"test:cli:create-tahoe-lafs")
@inlineCallbacks
def create(node_directory, configuration):
    argv = tahoe_lafs_args([
        b"create-node",
        b"--hostname", b"localhost",
        node_directory.path,
    ])
    yield _run_successfully(executable, argv)
    write_configuration(node_directory, to_configparser(configuration))


@inlineCallbacks
def create_introducer(testcase, introducer_directory):
    """
    Make a Tahoe-LAFS introducer node.

    :param testcase: A fixture-enabled test case instance which will be used
        to start and stop the Tahgoe-LAFS introducer process.

    :param FilePath introducer_directory: The path at which the introducer
        node will be created.

    :return Deferred[RunningTahoeLAFSNode]: A Deferred that fires with the
        fixture managing the running process.  The fixture is attached to
        ``testcase`` such that the process starts when the test starts and
        stops when the test stops.
    """
    yield create(introducer_directory, configuration={
        u"node": {},
    })
    # This actually makes it an introducer.
    introducer_directory.child(u"tahoe-introducer.tac").touch()
    introducer_directory.child(u"tahoe-client.tac").remove()

@inlineCallbacks
def _run_successfully(executable, argv):
    stdout, stderr, exit_code = yield getProcessOutputAndValue(
        executable,
        args=argv,
        env=environ,
    )
    Message.log(
        message_type=u"child-process:result",
        executable=executable,
        argv=argv,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )
    if exit_code != 0:
        raise ValueError("{} {} exited with failure code {}".format(
            executable,
            " ".join(argv),
            exit_code,
        ))
