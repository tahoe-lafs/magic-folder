from __future__ import (
    absolute_import,
    division,
    print_function,
)


from six.moves import (
    StringIO as MixedIO,
)
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


@attr.s
class ProcessOutcome(object):
    stdout = attr.ib()
    stderr = attr.ib()
    code = attr.ib()

    def succeeded(self):
        return self.code == 0

@inlineCallbacks
def cli(argv, global_config=None, http_client=None):
    """
    Perform an in-process equivalent to the given magic-folder command.

    :param list[bytes] argv: The magic-folder arguments which define the
        command to run.  This does not include "magic-folder" itself, just the
        following arguments.  For example, ``[b"list"]``.

    :param GlobalConfigDatabase global_config: The global configuration to use.

    :return Deferred[ProcessOutcome]: The side-effects and result of the
        process.
    """
    options = MagicFolderCommand()
    options.stdout = MixedIO()
    options.stderr = MixedIO()
    if global_config is not None:
        options._config = global_config
    if http_client is not None:
        options._http_client = http_client

    try:
        try:
            options.parseOptions(argv)
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
