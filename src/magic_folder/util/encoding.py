# Copyright 2021 The Magic-Folder Developers
# See COPYING for details.

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import unicodedata

import six
import yaml


def normalize(text):
    """
    :param unicode text: some unicode string

    :returns: the normalized form of the unicode in `text`.
    """
    return unicodedata.normalize("NFC", text)


if six.PY2:

    class _UnicodeLoader(yaml.SafeLoader):
        """
        A safe yaml loader that does not convert ascii strings to `bytes`.
        """

        def construct_yaml_str(self, node):
            return self.construct_scalar(node)

    _UnicodeLoader.add_constructor(
        "tag:yaml.org,2002:str", _UnicodeLoader.construct_yaml_str
    )
else:
    _UnicodeLoader = yaml.SafeLoader


def load_yaml(stream):
    """
    Parse the first YAML document in a stream.

    Returns string values as :py:`unicode`.
    """
    return yaml.load(stream, _UnicodeLoader)


def dump_yaml(data, stream=None):
    """
    Serialize an object into a YAML stream.
    """
    return yaml.safe_dump(data, stream=stream)
