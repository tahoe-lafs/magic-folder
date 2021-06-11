"""
Functions used to convert inputs from whatever encoding used in the system to
unicode and back.

Ported to Python 3.

Once Python 2 support is dropped, most of this module will obsolete, since
Unicode is the default everywhere in Python 3.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2, PY3, native_str
if PY2:
    # We omit str() because that seems too tricky to get right.
    from builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, max, min  # noqa: F401

from past.builtins import unicode

import sys, os, re, locale
import unicodedata
import warnings

from magic_folder._vendor.allmydata.util.assertutil import precondition, _assert
from twisted.python import usage
from twisted.python.filepath import FilePath
from magic_folder._vendor.allmydata.util import log

NoneType = type(None)


def canonical_encoding(encoding):
    if encoding is None:
        log.msg("Warning: falling back to UTF-8 encoding.", level=log.WEIRD)
        encoding = 'utf-8'
    encoding = encoding.lower()
    if encoding == "cp65001":
        encoding = 'utf-8'
    elif encoding == "us-ascii" or encoding == "646" or encoding == "ansi_x3.4-1968":
        encoding = 'ascii'

    return encoding

def check_encoding(encoding):
    # sometimes Python returns an encoding name that it doesn't support for conversion
    # fail early if this happens
    try:
        u"test".encode(encoding)
    except (LookupError, AttributeError):
        raise AssertionError("The character encoding '%s' is not supported for conversion." % (encoding,))

filesystem_encoding = None
io_encoding = None
is_unicode_platform = False
use_unicode_filepath = False

def _reload():
    global filesystem_encoding, io_encoding, is_unicode_platform, use_unicode_filepath

    filesystem_encoding = canonical_encoding(sys.getfilesystemencoding())
    check_encoding(filesystem_encoding)

    if sys.platform == 'win32':
        # On Windows we install UTF-8 stream wrappers for sys.stdout and
        # sys.stderr, and reencode the arguments as UTF-8 (see scripts/runner.py).
        io_encoding = 'utf-8'
    else:
        ioenc = None
        if hasattr(sys.stdout, 'encoding'):
            ioenc = sys.stdout.encoding
        if ioenc is None:
            try:
                ioenc = locale.getpreferredencoding()
            except Exception:
                pass  # work around <http://bugs.python.org/issue1443504>
        io_encoding = canonical_encoding(ioenc)

    check_encoding(io_encoding)

    is_unicode_platform = PY3 or sys.platform in ["win32", "darwin"]

    # Despite the Unicode-mode FilePath support added to Twisted in
    # <https://twistedmatrix.com/trac/ticket/7805>, we can't yet use
    # Unicode-mode FilePaths with INotify on non-Windows platforms due to
    # <https://twistedmatrix.com/trac/ticket/7928>. Supposedly 7928 is fixed,
    # though... and Tahoe-LAFS doesn't use inotify anymore!
    #
    # In the interest of not breaking anything, this logic is unchanged for
    # Python 2, but on Python 3 the paths are always unicode, like it or not.
    use_unicode_filepath = PY3 or sys.platform == "win32"

_reload()


def get_filesystem_encoding():
    """
    Returns expected encoding for local filenames.
    """
    return filesystem_encoding

def get_io_encoding():
    """
    Returns expected encoding for writing to stdout or stderr, and for arguments in sys.argv.
    """
    return io_encoding

def argv_to_unicode(s):
    """
    Decode given argv element to unicode. If this fails, raise a UsageError.
    """
    if isinstance(s, unicode):
        return s

    precondition(isinstance(s, bytes), s)

    try:
        return unicode(s, io_encoding)
    except UnicodeDecodeError:
        raise usage.UsageError("Argument %s cannot be decoded as %s." %
                               (quote_output(s), io_encoding))

def to_bytes(s):
    """Convert unicode to bytes.

    None and bytes are passed through unchanged.
    """
    if s is None or isinstance(s, bytes):
        return s
    return s.encode('utf-8')

def _unicode_escape(m, quote_newlines):
    u = m.group(0)
    if u == u'"' or u == u'$' or u == u'`' or u == u'\\':
        return u'\\' + u
    elif u == u'\n' and not quote_newlines:
        return u
    if len(u) == 2:
        codepoint = (ord(u[0])-0xD800)*0x400 + ord(u[1])-0xDC00 + 0x10000
    else:
        codepoint = ord(u)
    if codepoint > 0xFFFF:
        return u'\\U%08x' % (codepoint,)
    elif codepoint > 0xFF:
        return u'\\u%04x' % (codepoint,)
    else:
        return u'\\x%02x' % (codepoint,)

def _bytes_escape(m, quote_newlines):
    """
    Takes a re match on bytes, the result is escaped bytes of group(0).
    """
    c = m.group(0)
    if c == b'"' or c == b'$' or c == b'`' or c == b'\\':
        return b'\\' + c
    elif c == b'\n' and not quote_newlines:
        return c
    else:
        return b'\\x%02x' % (ord(c),)

MUST_DOUBLE_QUOTE_NL = re.compile(u'[^\\x20-\\x26\\x28-\\x7E\u00A0-\uD7FF\uE000-\uFDCF\uFDF0-\uFFFC]', re.DOTALL)
MUST_DOUBLE_QUOTE    = re.compile(u'[^\\n\\x20-\\x26\\x28-\\x7E\u00A0-\uD7FF\uE000-\uFDCF\uFDF0-\uFFFC]', re.DOTALL)

# if we must double-quote, then we have to escape ", $ and `, but need not escape '
ESCAPABLE_UNICODE = re.compile(u'([\uD800-\uDBFF][\uDC00-\uDFFF])|'  # valid surrogate pairs
                               u'[^ !#\\x25-\\x5B\\x5D-\\x5F\\x61-\\x7E\u00A0-\uD7FF\uE000-\uFDCF\uFDF0-\uFFFC]',
                               re.DOTALL)

ESCAPABLE_8BIT    = re.compile( br'[^ !#\x25-\x5B\x5D-\x5F\x61-\x7E]', re.DOTALL)

def quote_output(s, quotemarks=True, quote_newlines=None, encoding=None):
    """
    Encode either a Unicode string or a UTF-8-encoded bytestring for representation
    on stdout or stderr, tolerating errors. If 'quotemarks' is True, the string is
    always quoted; otherwise, it is quoted only if necessary to avoid ambiguity or
    control bytes in the output. (Newlines are counted as control bytes iff
    quote_newlines is True.)

    Quoting may use either single or double quotes. Within single quotes, all
    characters stand for themselves, and ' will not appear. Within double quotes,
    Python-compatible backslash escaping is used.

    If not explicitly given, quote_newlines is True when quotemarks is True.

    On Python 3, returns Unicode strings.
    """
    precondition(isinstance(s, (bytes, unicode)), s)
    encoding = encoding or io_encoding

    if quote_newlines is None:
        quote_newlines = quotemarks

    def _encode(s):
        if isinstance(s, bytes):
            try:
                s = s.decode('utf-8')
            except UnicodeDecodeError:
                return b'b"%s"' % (ESCAPABLE_8BIT.sub(lambda m: _bytes_escape(m, quote_newlines), s),)

        must_double_quote = quote_newlines and MUST_DOUBLE_QUOTE_NL or MUST_DOUBLE_QUOTE
        if must_double_quote.search(s) is None:
            try:
                out = s.encode(encoding)
                if quotemarks or out.startswith(b'"'):
                    return b"'%s'" % (out,)
                else:
                    return out
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass

        escaped = ESCAPABLE_UNICODE.sub(lambda m: _unicode_escape(m, quote_newlines), s)
        return b'"%s"' % (escaped.encode(encoding, 'backslashreplace'),)

    result = _encode(s)
    if PY3:
        # On Python 3 half of what this function does is unnecessary, since
        # sys.stdout typically expects Unicode. To ensure no encode errors, one
        # can do:
        #
        # sys.stdout.reconfigure(encoding=sys.stdout.encoding, errors="backslashreplace")
        #
        # Although the problem is that doesn't work in Python 3.6, only 3.7 or
        # later... For now not thinking about it, just returning unicode since
        # that is the right thing to do on Python 3.
        result = result.decode(encoding)
    return result
