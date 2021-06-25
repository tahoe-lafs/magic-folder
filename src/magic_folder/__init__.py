# Copyright (C) Least Authority TFA GmbH

from __future__ import (
    absolute_import,
    division,
    print_function,
)

import warnings
# see https://github.com/LeastAuthority/magic-folder/issues/305
warnings.filterwarnings("ignore", message=".*Python 2 is no longer supported by the Python core team.*")
# We use returnValue because we are using python 2. eliot uses a wrapper around inline_callbacks,
# so this warning is reporting that we are using that wrappaer.
# see https://twistedmatrix.com/trac/ticket/9590
warnings.filterwarnings("ignore", message=".*returnValue.* should only be invoked by functions decorated with inlineCallbacks.*")


__all__ = [
    "__version__",
]

from ._version import (
    __version__,
)
