"""
Testtools-style matchers useful to the Tahoe-LAFS test suite.
"""

import attr

from testtools.matchers import (
    Mismatch,
    AfterPreprocessing,
    MatchesStructure,
    MatchesDict,
    MatchesListwise,
    MatchesAll,
    MatchesPredicate,
    Always,
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)

from foolscap.furl import (
    decode_furl,
)

from allmydata.util import (
    base32,
)
from allmydata.node import (
    read_config,
)
from allmydata.crypto import (
    ed25519,
    error,
)

from treq import (
    content,
)

@attr.s
class MatchesNodePublicKey(object):
    """
    Match an object representing the node's private key.

    To verify, the private key is loaded from the node's private config
    directory at the time the match is checked.
    """
    basedir = attr.ib()

    def match(self, other):
        """
        Match a private key which is the same as the private key in the node at
        ``self.basedir``.

        :param other: A signing key (aka "private key") from
            ``allmydata.crypto.ed25519``.  This is the key to check against
            the node's key.

        :return Mismatch: If the keys don't match.
        """
        config = read_config(self.basedir, u"tub.port")
        privkey_bytes = config.get_private_config("node.privkey")
        private_key = ed25519.signing_keypair_from_string(privkey_bytes)[0]
        signature = ed25519.sign_data(private_key, b"")
        other_public_key = ed25519.verifying_key_from_signing_key(other)
        try:
            ed25519.verify_signature(other_public_key, signature, b"")
        except error.BadSignature:
            return Mismatch("The signature did not verify.")


def matches_storage_announcement(basedir, anonymous=True, options=None):
    """
    Match a storage announcement.

    :param bytes basedir: The path to the node base directory which is
        expected to emit the announcement.  This is used to determine the key
        which is meant to sign the announcement.

    :param bool anonymous: If True, matches a storage announcement containing
        an anonymous access fURL.  Otherwise, fails to match such an
        announcement.

    :param list[matcher]|NoneType options: If a list, matches a storage
        announcement containing a list of storage plugin options matching the
        elements of the list.  If None, fails to match an announcement with
        storage plugin options.

    :return: A matcher with the requested behavior.
    """
    announcement = {
        u"permutation-seed-base32": matches_base32(),
    }
    if anonymous:
        announcement[u"anonymous-storage-FURL"] = matches_furl()
    if options:
        announcement[u"storage-options"] = MatchesListwise(options)
    return MatchesStructure(
        # Has each of these keys with associated values that match
        service_name=Equals(u"storage"),
        ann=MatchesDict(announcement),
        signing_key=MatchesNodePublicKey(basedir),
    )


def matches_furl():
    """
    Match any Foolscap fURL byte string.
    """
    return AfterPreprocessing(decode_furl, Always())


def matches_base32():
    """
    Match any base32 encoded byte string.
    """
    return AfterPreprocessing(base32.a2b, Always())



class MatchesSameElements(object):
    """
    Match if the two-tuple value given contains two elements that are equal to
    each other.
    """
    def match(self, value):
        left, right = value
        return Equals(left).match(right)


def matches_response(code_matcher=Always(), headers_matcher=Always(), body_matcher=Always()):
    """
    Match a Treq response object with certain code and body.

    :param Matcher code_matcher: A matcher to apply to the response code.

    :param Matcher headers_matcher: A matcher to apply to the response headers
        (a ``twisted.web.http_headers.Headers`` instance).

    :param Matcher body_matcher: A matcher to apply to the response body.

    :return: A matcher.
    """
    return MatchesAll(
        MatchesStructure(
            code=code_matcher,
            headers=headers_matcher,
        ),
        AfterPreprocessing(
            lambda response: content(response),
            succeeded(body_matcher),
        ),
    )

def contained_by(container):
    """
    Match an element in the given container.

    :param container: Anything that supports being the right-hand operand to
        ``in``.

    :return: A matcher.
    """
    return MatchesPredicate(
        lambda element: element in container,
        "%r not found",
    )
