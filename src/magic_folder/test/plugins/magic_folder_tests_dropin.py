# This source file is not in a package directory.  It does not live in the
# magic_folder package hierarchy at all.  It lives in twisted.plugins.  Thus,
# we must use absolute imports for anything we want from magic_folder.

from magic_folder.test.common import (
    AdoptedServerPort,
)

adoptedEndpointParser = AdoptedServerPort()
