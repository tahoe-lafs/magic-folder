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
    returnValue,
)

import attr

from eliot import (
    start_action,
)
from eliot.twisted import inline_callbacks

from ...cli import (
    MagicFolderCommand,
    run_magic_folder_options,
)


@attr.s(frozen=True)
class ProcessOutcome(object):
    stdout = attr.ib()
    stderr = attr.ib()
    code = attr.ib()

    def succeeded(self):
        return self.code == 0

@inline_callbacks
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
    with start_action(action_type="run-cli", argv=argv) as action:
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

        action.add_success_fields(
            code=result,
            stdout=options.stdout.getvalue(),
            stderr=options.stderr.getvalue(),
        )

    returnValue(ProcessOutcome(
        options.stdout.getvalue(),
        options.stderr.getvalue(),
        result,
    ))
