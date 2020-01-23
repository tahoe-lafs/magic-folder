from twisted.application.service import (
    ServiceMaker,
)

magic_folder = ServiceMaker(
    "Magic-Folder for Tahoe-LAFS",
    "magic_folder.scripts.magic_folder_cli",
    "Tahoe-LAFS-based file synchronization",
    "magic_folder",
)
