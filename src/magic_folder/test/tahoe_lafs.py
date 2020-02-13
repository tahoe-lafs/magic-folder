# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functionality for operating on a Tahoe-LAFS node.
"""

from __future__ import (
    absolute_import,
)

from sys import (
    executable,
)

from ConfigParser import (
    ConfigParser,
)

from eliot import (
    Message,
)

from allmydata.util.eliotutil import (
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
    yield _runSuccessfully(executable, argv)
    write_configuration(node_directory, to_configparser(configuration))

@inlineCallbacks
def _runSuccessfully(executable, argv):
    stdout, stderr, exit_code = yield getProcessOutputAndValue(
        executable,
        argv,
    )
    Message.log(
        message_type=u"stdout",
        value=stdout,
    )
    Message.log(
        message_type=u"stderr",
        value=stderr,
    )
    if exit_code != 0:
        raise RuntimeError("Darn")
