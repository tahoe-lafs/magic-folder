# Copyright 2021 The Magic-Folder Developers
# See COPYING for details.

from __future__ import (
    absolute_import,
    division,
    print_function,
)

from __future__ import (
    print_function,
    unicode_literals,
)

import unicodedata


def normalize(text):
    """
    :param unicode text: some unicode string

    :returns: the normalized form of the unicode in `text`.
    """
    return unicodedata.normalize("NFC", text)
