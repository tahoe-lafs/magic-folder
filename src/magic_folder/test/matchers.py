"""
Testtools-style matchers useful to the Tahoe-LAFS test suite.
"""

import base64
from uuid import (
    UUID,
)
from nacl.exceptions import (
    BadSignatureError,
)

import attr

from testtools.matchers import (
    Mismatch,
    AfterPreprocessing,
    MatchesStructure,
    MatchesAll,
    MatchesPredicate,
    MatchesException,
    ContainsDict,
    Always,
    Equals,
)
from testtools.twistedsupport import (
    succeeded,
)

from treq import (
    content,
)

@attr.s
class MatchesAuthorSignature(object):
    """
    Confirm signatures on a RemoteSnapshot
    """
    snapshot = attr.ib()  # LocalSnapshot
    remote_snapshot = attr.ib()

    def match(self, other):
        # "other" is the RemoteSnapshot's signature
        public_key = self.snapshot.author.verify_key
        alleged_sig = base64.b64decode(self.remote_snapshot.signature)
        signed_data = (
            u"{content_capability}\n"
            u"{name}\n"
        ).format(
            content_capability=self.remote_snapshot.content_cap,
            name=self.remote_snapshot.metadata['name'],
        ).encode("utf8")

        try:
            public_key.verify(signed_data, alleged_sig)
        except BadSignatureError:
            return Mismatch("The signature did not verify.")


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
    matchers = [
        MatchesStructure(
            code=code_matcher,
            headers=headers_matcher,
        ),
    ]
    # see comment in test_web.MagicFolderTests.test_method_not_allowed
    # which is one user that wants nothing to try and read the content
    # in some cases..
    if body_matcher is not None:
        matchers.append(
            AfterPreprocessing(
                lambda response: content(response),
                succeeded(body_matcher),
            )
        )
    return MatchesAll(*matchers)

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


def header_contains(header_dict):
    """
    Match a ``twisted.web.http_headers.HTTPHeaders`` containing at least the
    given items.

    :param dict[bytes, Matcher] header_dict: A dictionary mapping header
        names (canonical case) to matchers for the associated values (a list
        of byte-strings).

    :return: A matcher.
    """
    return AfterPreprocessing(
        lambda headers: dict(headers.getAllRawHeaders()),
        ContainsDict(header_dict),
    )


def provides(*interfaces):
    """
    Match an object that provides all of the given interfaces.

    :param InterfaceClass *interfaces: The required interfaces.

    :return: A matcher.
    """
    return MatchesAll(*list(
        MatchesPredicate(
            lambda obj, iface=iface: iface.providedBy(obj),
            "%s does not provide {!r}".format(iface),
        )
        for iface
        in interfaces
    ))


def is_hex_uuid():
    """
    Match text strings giving a hex representation of a UUID.

    :return: A matcher.
    """
    def _is_hex_uuid(value):
        if not isinstance(value, str):
            return False
        try:
            UUID(hex=value)
        except ValueError:
            return False
        return True
    return MatchesPredicate(
        _is_hex_uuid,
        "%r is not a UUID hex representation.",
    )


def matches_flushed_traceback(exception, value_re=None):
    """
    Matches an eliot traceback message with the given exception.

    This is expected to be used with :py:`testtools.matchers.MatchesListwise`,
    on the result of :py:`eliot.MemoryLogger.flush_tracebacks`.

    See :py:`testtools.matchers.MatchesExeption`.
    """
    def as_exc_info_tuple(message):
        return message["exception"], message["reason"], message["traceback"]

    return AfterPreprocessing(
        as_exc_info_tuple, MatchesException(exception, value_re=value_re)
    )

def matches_failure(exception, value_re=None):
    """
    Matches an twisted :py:`Failure` with the given exception.

    See :py:`testtools.matches.MatchesException`.
    """
    def as_exc_info_tuple(failure):
        return failure.type, failure.value, failure.tb

    return AfterPreprocessing(
        as_exc_info_tuple, MatchesException(exception, value_re=value_re)
    )
