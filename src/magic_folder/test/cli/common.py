from __future__ import (
    print_function,
)

from six.moves import (
    StringIO as MixedIO,
)
from allmydata.scripts import runner

from twisted.python.usage import (
    UsageError,
)
from twisted.internet.defer import (
    inlineCallbacks,
    returnValue,
)

import attr

from eliot import (
    Message,
)

from ...cli import (
    MagicFolderCommand,
    run_magic_folder_options,
)

def parse_options(basedir, command, args):
    o = runner.Options()
    o.parseOptions(["--node-directory", basedir, command] + args)
    while hasattr(o, "subOptions"):
        o = o.subOptions
    return o


@attr.s
class ProcessOutcome(object):
    stdout = attr.ib()
    stderr = attr.ib()
    code = attr.ib()

    def succeeded(self):
        return self.code == 0

@inlineCallbacks
def cli(config_directory, argv):
    """
    Perform an in-process equivalent to the given magic-folder command.

    :param FilePath config_directory: The path to our configuration

    :param list[bytes] argv: The magic-folder arguments which define the
        command to run.  This does not include "magic-folder" itself, just the
        following arguments.  For example, ``[b"list"]``.

    :return Deferred[ProcessOutcome]: The side-effects and result of the
        process.
    """
    options = MagicFolderCommand()
    options.stdout = MixedIO()
    options.stderr = MixedIO()
    try:
        try:
            options.parseOptions([
                b"--config",
                config_directory.asBytesMode().path,
            ] + argv)
        except UsageError as e:
            print(e, file=options.stderr)
            result = 1
        else:
            result = yield run_magic_folder_options(options)
            if result is None:
                result = 0
    except SystemExit as e:
        result = e.code

    Message.log(
        message_type=u"stdout",
        value=options.stdout.getvalue(),
    )
    Message.log(
        message_type=u"stderr",
        value=options.stderr.getvalue(),
    )

    returnValue(ProcessOutcome(
        options.stdout.getvalue(),
        options.stderr.getvalue(),
        result,
    ))
