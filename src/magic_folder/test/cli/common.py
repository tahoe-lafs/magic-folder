from contextlib import contextmanager
from io import StringIO

from twisted.python import usage
from twisted.python.usage import (
    UsageError,
)
from twisted.internet.defer import (
    returnValue,
)
from twisted.internet.task import Cooperator

import attr

from eliot import (
    start_action,
)
from eliot.twisted import inline_callbacks

from ...cli import (
    MagicFolderCommand,
    run_magic_folder_options,
)
from ...api_cli import (
    MagicFolderApiCommand,
    run_magic_folder_api_options,
)



@contextmanager
def _pump_client(http_client):
    """
    Periodically flush all data in the HTTP client while this context manager
    is active.

    py:`treq.testing.RequestTraversalAgent` doesn't automatically pass data
    between a client and server, if a response isn't generated synchronously,
    so if we want requests to complete, we need to call ``.flush`` on it.

    :param treq.client.HTTPClient http_client:
        An HTTP Client wrapping a :py:`treq.testing.RequestTraversalAgent`.
    """
    def pump():
        while True:
            http_client._agent.flush()
            yield

    coop = Cooperator()
    task = coop.cooperate(pump())
    try:
        yield
    finally:
        task.stop()
        coop.stop()

def _subclass_of(base):
    """
    Construct an attrs validator that raises :py:`TypeError` is not a subclass
    of the given base class.

    :param type base: The given base class

    :return Callable[[Any, attr.Attribute, Any], None]: An attrs validator.
    """
    def validator(inst, attr, value):
        if not issubclass(value, base):
            raise TypeError(
                "'{name}' must be a subclass of {base!r}"
                "(got {value!r}).".format(
                    name=attr.name,
                    base=base,
                    value=value,
                )
            )

    return validator


@attr.s
class Command(object):
    """
    A magic-folder command entry point.

    :ivar Type[usage.Options] options: The options class to use.
    :ivar Callable[[usage.Options], Deferred[None]] entry_point:
        The function to pass the options to.
    """
    options = attr.ib(validator=_subclass_of(usage.Options))
    entry_point = attr.ib(validator=attr.validators.is_callable())



@attr.s(frozen=True)
class ProcessOutcome(object):
    stdout = attr.ib()
    stderr = attr.ib()
    code = attr.ib()

    def succeeded(self):
        return self.code == 0

@inline_callbacks
def _run_cli(command, argv, global_config=None, http_client=None):
    """
    Perform an in-process equivalent to the given command.

    This will pump the provided HTTP client, to allow asynchronous endpoints
    to work.

    :param Command command: the comamnd to use
    :param list[bytes] argv: The magic-folder arguments which define the
        command to run.  This does not include "magic-folder" itself, just the
        following arguments.  For example, ``[b"list"]``.

    :param GlobalConfigDatabase global_config: The global configuration to use.
    :param treq.HTTPClient http_client: A :py:`treq.HTTPClient` wrapping an
        :py:`treq.testing.RequestTraversalAgent`.

    :return Deferred[ProcessOutcome]: The side-effects and result of the
        process.
    """
    with start_action(action_type="run-cli", argv=argv) as action:
        options = command.options()
        options.stdout = StringIO()
        options.stderr = StringIO()
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
                with _pump_client(http_client):
                    result = yield command.entry_point(options)
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

def cli(argv, global_config=None, http_client=None):
    """
    Perform an in-process equivalent to the given magic-folder command.

    This will pump the provided HTTP client, to allow asynchronous endpoints
    to work.

    :param list[bytes] argv: The magic-folder arguments which define the
        command to run.  This does not include "magic-folder" itself, just the
        following arguments.  For example, ``[b"list"]``.

    :param GlobalConfigDatabase global_config: The global configuration to use.
    :param treq.HTTPClient http_client: A :py:`treq.HTTPClient` wrapping an
        :py:`treq.testing.RequestTraversalAgent`.

    :return Deferred[ProcessOutcome]: The side-effects and result of the
        process.
    """
    command = Command(MagicFolderCommand, run_magic_folder_options)
    return _run_cli(command, argv, global_config, http_client)

def api_cli(argv, global_config=None, http_client=None):
    """
    Perform an in-process equivalent to the given magic-folder-api command.

    This will pump the provided HTTP client, to allow asynchronous endpoints
    to work.

    :param list[bytes] argv: The magic-folder arguments which define the
        command to run.  This does not include "magic-folder-api" itself, just the
        following arguments.  For example, ``[b"add-snapshot"]``.

    :param GlobalConfigDatabase global_config: The global configuration to use.
    :param treq.HTTPClient http_client: A :py:`treq.HTTPClient` wrapping an
        :py:`treq.testing.RequestTraversalAgent`.

    :return Deferred[ProcessOutcome]: The side-effects and result of the
        process.
    """
    command = Command(MagicFolderApiCommand, run_magic_folder_api_options)
    return _run_cli(command, argv, global_config, http_client)
