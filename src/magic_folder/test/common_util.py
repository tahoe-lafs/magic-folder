from __future__ import absolute_import, division, print_function

from ..cli import MagicFolderCommand


def parse_cli(*argv):
    # This parses the CLI options (synchronously), and returns the Options
    # argument, or throws usage.UsageError if something went wrong.
    options = MagicFolderCommand()
    options.parseOptions(argv)
    return options
