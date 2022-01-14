# Copyright 2021 The Magic-Folder Developers
# See COPYING for details.

import unicodedata
import yaml


def normalize(text):
    """
    :param unicode text: some unicode string

    :returns: the normalized form of the unicode in `text`.
    """
    return unicodedata.normalize("NFC", text)


def load_yaml(stream):
    """
    Parse the first YAML document in a stream.

    Returns string values as :py:`unicode`.
    """
    return yaml.load(stream, yaml.SafeLoader)


def dump_yaml(data, stream=None):
    """
    Serialize an object into a YAML stream.
    """
    return yaml.safe_dump(data, stream=stream)
