# Copyright (c) Least Authority TFA GmbH.
# See COPYING.* for details.

from __future__ import (
    absolute_import,
    division,
    print_function,
)

if __name__ == '__main__':
    from pkg_resources import load_entry_point
    import sys

    sys.exit(
        load_entry_point('magic-folder', 'console_scripts', 'magic-folder')()
    )
