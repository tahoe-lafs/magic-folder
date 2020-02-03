"""
Functions used to convert inputs from whatever encoding used in the system to
unicode and back.
"""

import sys, os, re, locale
from types import NoneType

from allmydata.util.assertutil import precondition, _assert
from twisted.python import usage
from twisted.python.filepath import FilePath
from allmydata.util import log
from allmydata.util.fileutil import abspath_expanduser_unicode


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

    is_unicode_platform = sys.platform in ["win32", "darwin"]

    # Despite the Unicode-mode FilePath support added to Twisted in
    # <https://twistedmatrix.com/trac/ticket/7805>, we can't yet use
    # Unicode-mode FilePaths with INotify on non-Windows platforms
    # due to <https://twistedmatrix.com/trac/ticket/7928>.
    use_unicode_filepath = sys.platform == "win32"

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
    precondition(isinstance(s, str), s)

    try:
        return unicode(s, io_encoding)
    except UnicodeDecodeError:
        raise usage.UsageError("Argument %s cannot be decoded as %s." %
                               (quote_output(s), io_encoding))

def argv_to_abspath(s, **kwargs):
    """
    Convenience function to decode an argv element to an absolute path, with ~ expanded.
    If this fails, raise a UsageError.
    """
    decoded = argv_to_unicode(s)
    if decoded.startswith(u'-'):
        raise usage.UsageError("Path argument %s cannot start with '-'.\nUse %s if you intended to refer to a file."
                               % (quote_output(s), quote_output(os.path.join('.', s))))
    return abspath_expanduser_unicode(decoded, **kwargs)

def unicode_to_argv(s, mangle=False):
    """
    Encode the given Unicode argument as a bytestring.
    If the argument is to be passed to a different process, then the 'mangle' argument
    should be true; on Windows, this uses a mangled encoding that will be reversed by
    code in runner.py.
    """
    precondition(isinstance(s, unicode), s)

    if mangle and sys.platform == "win32":
        # This must be the same as 'mangle' in bin/tahoe-script.template.
        return str(re.sub(u'[^\\x20-\\x7F]', lambda m: u'\x7F%x;' % (ord(m.group(0)),), s))
    else:
        return s.encode(io_encoding)

def unicode_to_url(s):
    """
    Encode an unicode object used in an URL.
    """
    # According to RFC 2718, non-ascii characters in URLs must be UTF-8 encoded.

    # FIXME
    return to_str(s)
    #precondition(isinstance(s, unicode), s)
    #return s.encode('utf-8')

def to_str(s):
    if s is None or isinstance(s, str):
        return s
    return s.encode('utf-8')

def from_utf8_or_none(s):
    precondition(isinstance(s, (NoneType, str)), s)
    if s is None:
        return s
    return s.decode('utf-8')

PRINTABLE_ASCII = re.compile(r'^[\n\r\x20-\x7E]*$',          re.DOTALL)
PRINTABLE_8BIT  = re.compile(r'^[\n\r\x20-\x7E\x80-\xFF]*$', re.DOTALL)

def is_printable_ascii(s):
    return PRINTABLE_ASCII.search(s) is not None

def unicode_to_output(s):
    """
    Encode an unicode object for representation on stdout or stderr.
    """
    precondition(isinstance(s, unicode), s)

    try:
        out = s.encode(io_encoding)
    except (UnicodeEncodeError, UnicodeDecodeError):
        raise UnicodeEncodeError(io_encoding, s, 0, 0,
                                 "A string could not be encoded as %s for output to the terminal:\n%r" %
                                 (io_encoding, repr(s)))

    if PRINTABLE_8BIT.search(out) is None:
        raise UnicodeEncodeError(io_encoding, s, 0, 0,
                                 "A string encoded as %s for output to the terminal contained unsafe bytes:\n%r" %
                                 (io_encoding, repr(s)))
    return out


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

def _str_escape(m, quote_newlines):
    c = m.group(0)
    if c == '"' or c == '$' or c == '`' or c == '\\':
        return '\\' + c
    elif c == '\n' and not quote_newlines:
        return c
    else:
        return '\\x%02x' % (ord(c),)

MUST_DOUBLE_QUOTE_NL = re.compile(u'[^\\x20-\\x26\\x28-\\x7E\u00A0-\uD7FF\uE000-\uFDCF\uFDF0-\uFFFC]', re.DOTALL)
MUST_DOUBLE_QUOTE    = re.compile(u'[^\\n\\x20-\\x26\\x28-\\x7E\u00A0-\uD7FF\uE000-\uFDCF\uFDF0-\uFFFC]', re.DOTALL)

# if we must double-quote, then we have to escape ", $ and `, but need not escape '
ESCAPABLE_UNICODE = re.compile(u'([\uD800-\uDBFF][\uDC00-\uDFFF])|'  # valid surrogate pairs
                               u'[^ !#\\x25-\\x5B\\x5D-\\x5F\\x61-\\x7E\u00A0-\uD7FF\uE000-\uFDCF\uFDF0-\uFFFC]',
                               re.DOTALL)

ESCAPABLE_8BIT    = re.compile( r'[^ !#\x25-\x5B\x5D-\x5F\x61-\x7E]', re.DOTALL)

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
    """
    precondition(isinstance(s, (str, unicode)), s)
    if quote_newlines is None:
        quote_newlines = quotemarks

    if isinstance(s, str):
        try:
            s = s.decode('utf-8')
        except UnicodeDecodeError:
            return 'b"%s"' % (ESCAPABLE_8BIT.sub(lambda m: _str_escape(m, quote_newlines), s),)

    must_double_quote = quote_newlines and MUST_DOUBLE_QUOTE_NL or MUST_DOUBLE_QUOTE
    if must_double_quote.search(s) is None:
        try:
            out = s.encode(encoding or io_encoding)
            if quotemarks or out.startswith('"'):
                return "'%s'" % (out,)
            else:
                return out
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    escaped = ESCAPABLE_UNICODE.sub(lambda m: _unicode_escape(m, quote_newlines), s)
    return '"%s"' % (escaped.encode(encoding or io_encoding, 'backslashreplace'),)

def quote_path(path, quotemarks=True):
    return quote_output("/".join(map(to_str, path)), quotemarks=quotemarks, quote_newlines=True)

def quote_local_unicode_path(path, quotemarks=True):
    precondition(isinstance(path, unicode), path)

    if sys.platform == "win32" and path.startswith(u"\\\\?\\"):
        path = path[4 :]
        if path.startswith(u"UNC\\"):
            path = u"\\\\" + path[4 :]

    return quote_output(path, quotemarks=quotemarks, quote_newlines=True)

def quote_filepath(path, quotemarks=True):
    return quote_local_unicode_path(unicode_from_filepath(path), quotemarks=quotemarks)

def extend_filepath(fp, segments):
    # We cannot use FilePath.preauthChild, because
    # * it has the security flaw described in <https://twistedmatrix.com/trac/ticket/6527>;
    # * it may return a FilePath in the wrong mode.

    for segment in segments:
        fp = fp.child(segment)

    if isinstance(fp.path, unicode) and not use_unicode_filepath:
        return FilePath(fp.path.encode(filesystem_encoding))
    else:
        return fp

def to_filepath(path):
    precondition(isinstance(path, unicode if use_unicode_filepath else basestring),
                 path=path)

    if isinstance(path, unicode) and not use_unicode_filepath:
        path = path.encode(filesystem_encoding)

    if sys.platform == "win32":
        _assert(isinstance(path, unicode), path=path)
        if path.startswith(u"\\\\?\\") and len(path) > 4:
            # FilePath normally strips trailing path separators, but not in this case.
            path = path.rstrip(u"\\")

    return FilePath(path)

def _decode(s):
    precondition(isinstance(s, basestring), s=s)

    if isinstance(s, bytes):
        return s.decode(filesystem_encoding)
    else:
        return s

def unicode_from_filepath(fp):
    precondition(isinstance(fp, FilePath), fp=fp)
    return _decode(fp.path)

def unicode_segments_from(base_fp, ancestor_fp):
    precondition(isinstance(base_fp, FilePath), base_fp=base_fp)
    precondition(isinstance(ancestor_fp, FilePath), ancestor_fp=ancestor_fp)

    return base_fp.asTextMode().segmentsFrom(ancestor_fp.asTextMode())

def unicode_platform():
    """
    Does the current platform handle Unicode filenames natively?
    """
    return is_unicode_platform

class FilenameEncodingError(Exception):
    """
    Filename cannot be encoded using the current encoding of your filesystem
    (%s). Please configure your locale correctly or rename this file.
    """
    pass

def listdir_unicode_fallback(path):
    """
    This function emulates a fallback Unicode API similar to one available
    under Windows or MacOS X.

    If badly encoded filenames are encountered, an exception is raised.
    """
    precondition(isinstance(path, unicode), path)

    try:
        byte_path = path.encode(filesystem_encoding)
    except (UnicodeEncodeError, UnicodeDecodeError):
        raise FilenameEncodingError(path)

    try:
        return [unicode(fn, filesystem_encoding) for fn in os.listdir(byte_path)]
    except UnicodeDecodeError as e:
        raise FilenameEncodingError(e.object)

def listdir_unicode(path):
    """
    Wrapper around listdir() which provides safe access to the convenient
    Unicode API even under platforms that don't provide one natively.
    """
    precondition(isinstance(path, unicode), path)

    # On Windows and MacOS X, the Unicode API is used
    # On other platforms (ie. Unix systems), the byte-level API is used

    if is_unicode_platform:
        return os.listdir(path)
    else:
        return listdir_unicode_fallback(path)

def listdir_filepath(fp):
    return listdir_unicode(unicode_from_filepath(fp))
