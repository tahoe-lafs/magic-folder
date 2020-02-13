# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Functionality for operating on a Tahoe-LAFS node.
"""

from __future__ import (
    absolute_import,
)

from twisted.internet.defer import (
    inlineCallbacks,
)

def tahoe_lafs_args(argv):
    return [b"-m", b"allmydata.scripts.runner"] + argv

def to_configparser(configuration):
    configparser = ConfigParser()
    for section, items in configuration.items():
        for (key, value) in items.items():
            configparser.set(section, key, value)
    return configparser

def write_configuration(node_directory, config):
    with node_directory.child(b"tahoe.cfg", "w") as fobj:
        config.write(fobj)

@inlineCallbacks
def create(node_directory, configuration):
    argv = tahoe_lafs_args([b"create-node", node_directory.path])
    output, exit_code = yield getProcessOutputAndValue(
        executable,
        argv,
    )
    print(output)
    if exit_code != 0:
        raise RuntimeError("Darn")
    write_configuration(node_directory, to_configparser(configuration))
