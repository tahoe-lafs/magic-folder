from __future__ import print_function
from __future__ import unicode_literals

import sys

from twisted.internet.task import (
    react,
)

from twisted.python.filepath import (
    FilePath,
)
from twisted.python import usage
from twisted.internet.defer import (
    maybeDeferred,
    inlineCallbacks,
)

from .cli import (
    _default_config_path,
    load_global_configuration,
)
from .client import (
    CannotAccessAPIError,
    create_http_client,
    create_magic_folder_client,
)


class AddSnapshotOptions(usage.Options):
    optParameters = [
        ("file", "f", None, "Path of the file to add a Snapshot of"),
        ("folder", "n", None, "Name of the magic-folder to add the Snapshot to"),
    ]

    def postOptions(self):
        # required args
        if self['file'] is None:
            raise usage.UsageError("--file / -f is required")
        if self['folder'] is None:
            raise usage.UsageError("--folder / -n is required")


@inlineCallbacks
def add_snapshot(options):
    """
    Add one new Snapshot of a particular file in a particular
    magic-folder.
    """
    from twisted.internet import reactor
    client = create_magic_folder_client(
        reactor,
        options.parent.config,
        create_http_client(reactor, options.parent.config.api_client_endpoint),
    )
    res = yield client.add_snapshot(
        options['folder'].decode("utf8"),
        options['file'].decode("utf8"),
    )
    print("{}".format(res))


class MagicFolderApiCommand(usage.Options):
    """
    top-level command (entry-point is "magic-folder-api")
    """
    stdin = sys.stdin
    stdout = sys.stdout
    stderr = sys.stderr

    optFlags = [
        ["version", "V", "Display version numbers."],
    ]
    optParameters = [
        ("config", "c", _default_config_path,
         "The directory containing configuration"),
    ]

    _config = None  # lazy-instantiated by .config @property

    @property
    def _config_path(self):
        """
        The FilePath where our config is located
        """
        return FilePath(self['config'])

    @property
    def config(self):
        """
        a GlobalConfigDatabase instance representing the current
        configuration location.
        """
        if self._config is None:
            try:
                self._config = load_global_configuration(self._config_path)
            except Exception as e:
                raise usage.UsageError(
                    u"Unable to load configuration: {}".format(e)
                )
        return self._config

    subCommands = [
        ["add-snapshot", None, AddSnapshotOptions, "Add a Snapshot of a file to a magic-folder."],
    ]
    optFlags = [
        ["debug", "d", "Print full stack-traces"],
    ]
    description = (
        "Convenience wrappers around the Magic Folder local "
        "HTTP API. Handles authentication and encoding"
    )

    @property
    def parent(self):
        return None

    @parent.setter
    def parent(self, ignored):
        pass

    def opt_version(self):
        """
        Display magic-folder version and exit.
        """
        from . import __version__
        print("magic-folder-api version {}".format(__version__))
        sys.exit(0)

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")

    def getSynopsis(self):
        return "Usage: magic-folder-api [global-options] <subcommand> [subcommand-options]"

    def getUsage(self, width=None):
        t = usage.Options.getUsage(self, width)
        t += (
            "Please run e.g. 'magic-folder-api add-snapshot --help' for more "
            "details on each subcommand.\n"
        )
        return t


@inlineCallbacks
def dispatch_magic_folder_api_command(args):
    """
    Run a magic-folder-api command with the given args

    :returns: a Deferred which fires with the result of doing this
        magic-folder-api (sub)command.
    """
    options = MagicFolderApiCommand()
    try:
        options.parseOptions(args)
    except usage.UsageError as e:
        print("Error: {}".format(e))
        # if a user just typed "magic-folder-api" don't make them re-run
        # with "--help" just to see the sub-commands they were
        # supposed to use
        if len(sys.argv) == 1:
            print(options)
        raise SystemExit(1)

    yield run_magic_folder_api_options(options)


@inlineCallbacks
def run_magic_folder_api_options(options):
    """
    Runs a magic-folder-api subcommand with the provided options.

    :param options: already-parsed options.

    :returns: a Deferred which fires with the result of doing this
        magic-folder-api (sub)command.
    """
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    main_func = {
        "add-snapshot": add_snapshot,
    }[options.subCommand]

    # we want to let exceptions out to the top level if --debug is on
    # because this gives better stack-traces
    if options['debug']:
        yield maybeDeferred(main_func, so)

    else:
        try:
            yield maybeDeferred(main_func, so)

        except CannotAccessAPIError as e:
            # give user more information if we can't find the daemon at all
            print(u"Error: {}".format(e), file=options.stderr)
            print(u"   Attempted access via {}".format(options.config.api_client_endpoint))
            raise SystemExit(1)

        except Exception as e:
            print(u"Error: {}".format(e), file=options.stderr)
            raise SystemExit(1)


def _entry():
    """
    Implement the *magic-folder-api* console script declared in ``setup.py``.

    :return: ``None``
    """

    def main(reactor):
        return dispatch_magic_folder_api_command(sys.argv[1:])
    return react(main)
