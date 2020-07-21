# Copyright (C) Least Authority TFA GmbH

__all__ = [
    "__version__",
]

from ._version import (
    __version__,
)

def _hotfixes():
    from ._tahoe_lafs_3349 import (
        hotfix,
    )
    hotfix()

_hotfixes()
