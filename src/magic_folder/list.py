# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Implements ```magic-folder list``` command.
"""

import json

from eliot.twisted import (
    inline_callbacks,
)

@inline_callbacks
def magic_folder_list(reactor, config, client, output, as_json=False, include_secret_information=False):
    """
    List folders associated with a node.

    :param GlobalConfigDatabase config: our configuration

    :param MagicFolderClient client: a client that connects to the
        magic-folder API.

    :param output: a file-like object to which the output will be written

    :param bool as_json: return answer in JSON

    :param bool include_secret_information: include sensitive private
        information (such as long-term keys) if True (default: False).

    :return: JSON response from `GET /v1/magic-folder`.
    """
    mf_info = yield client.list_folders(include_secret_information)

    if as_json:
        output.write(u"{}\n".format(json.dumps(mf_info, indent=4)))
        return
    _list_human(mf_info, output, include_secret_information)


def _list_human(info, output, include_secrets):
    """
    List our magic-folders for a human user.
    """
    if include_secrets:
        template = (
            u"    location: {magic_path}\n"
            u"   stash-dir: {stash_path}\n"
            u"      author: {author[name]} (private_key: {author[signing_key]})\n"
            u"  collective: {collective_dircap}\n"
            u"    personal: {upload_dircap}\n"
            u"     updates: every {poll_interval}s\n"
            u"       admin: {is_admin}\n"
        )
    else:
        template = (
            u"    location: {magic_path}\n"
            u"   stash-dir: {stash_path}\n"
            u"      author: {author[name]} (public_key: {author[verify_key]})\n"
            u"     updates: every {poll_interval}s\n"
            u"       admin: {is_admin}\n"
        )

    if info:
        output.write(u"Configured magic-folders:\n")
        for name, details in info.items():
            output.write(u"{}:\n".format(name))
            output.write(template.format(**details))
    else:
        output.write(u"No magic-folders")
