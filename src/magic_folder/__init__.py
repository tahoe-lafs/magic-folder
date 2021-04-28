# Copyright (C) Least Authority TFA GmbH

# see https://github.com/LeastAuthority/magic-folder/issues/305
import warnings
warnings.filterwarnings("ignore", module="OpenSSL.crypto.*", lineno=14)

__all__ = [
    "__version__",
]

from ._version import (
    __version__,
)
