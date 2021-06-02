from __future__ import (
    absolute_import,
    division,
    print_function,
)

from twisted.application.service import (
    ServiceMaker,
)

magic_folder = ServiceMaker(
    "Magic-Folder for Tahoe-LAFS",
    "magic_folder.cli",
    "Tahoe-LAFS-based file synchronization",
    "magic_folder",
)
