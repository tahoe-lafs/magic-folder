from __future__ import print_function

lumiere_nfc = u"lumi\u00E8re"
Artonwall_nfc = u"\u00C4rtonwall.mp3"
Artonwall_nfd = u"A\u0308rtonwall.mp3"

TEST_FILENAMES = (
  Artonwall_nfc,
  u'test_file',
  u'Blah blah.txt',
)

# The following main helps to generate a test class for other operating
# systems.

if __name__ == "__main__":
    import sys, os
    import tempfile
    import shutil
    import platform

    if len(sys.argv) != 2:
        print("Usage: %s lumi<e-grave>re" % sys.argv[0])
        sys.exit(1)

    if sys.platform == "win32":
        try:
            from allmydata.windows.fixups import initialize
        except ImportError:
            print("set PYTHONPATH to the src directory")
            sys.exit(1)
        initialize()

    print()
    print("class MyWeirdOS(EncodingUtil, unittest.TestCase):")
    print("    uname = '%s'" % ' '.join(platform.uname()))
    print("    argv = %s" % repr(sys.argv[1]))
    print("    platform = '%s'" % sys.platform)
    print("    filesystem_encoding = '%s'" % sys.getfilesystemencoding())
    print("    io_encoding = '%s'" % sys.stdout.encoding)
    try:
        tmpdir = tempfile.mkdtemp()
        for fname in TEST_FILENAMES:
            open(os.path.join(tmpdir, fname), 'w').close()

        # Use Unicode API under Windows or MacOS X
        if sys.platform in ('win32', 'darwin'):
            dirlist = os.listdir(unicode(tmpdir))
        else:
            dirlist = os.listdir(tmpdir)

        print("    dirlist = %s" % repr(dirlist))
    except:
        print("    # Oops, I cannot write filenames containing non-ascii characters")
    print()

    shutil.rmtree(tmpdir)
    sys.exit(0)


import os, sys, locale

from twisted.trial import unittest

from twisted.python.filepath import FilePath

from allmydata.test.common_util import ReallyEqualMixin
from allmydata.util import encodingutil, fileutil
from allmydata.util.encodingutil import argv_to_unicode, unicode_to_url, \
    unicode_to_output, quote_output, quote_path, quote_local_unicode_path, \
    quote_filepath, unicode_platform, listdir_unicode, FilenameEncodingError, \
    get_io_encoding, get_filesystem_encoding, to_str, from_utf8_or_none, _reload, \
    to_filepath, extend_filepath, unicode_from_filepath, unicode_segments_from
from allmydata.dirnode import normalize
from .common_util import skip_if_cannot_represent_filename
from twisted.python import usage


class MockStdout(object):
    pass

class EncodingUtilErrors(ReallyEqualMixin, unittest.TestCase):
    def test_get_io_encoding(self):
        mock_stdout = MockStdout()
        self.patch(sys, 'stdout', mock_stdout)

        mock_stdout.encoding = 'UTF-8'
        _reload()
        self.failUnlessReallyEqual(get_io_encoding(), 'utf-8')

        mock_stdout.encoding = 'cp65001'
        _reload()
        self.failUnlessReallyEqual(get_io_encoding(), 'utf-8')

        mock_stdout.encoding = 'koi8-r'
        expected = sys.platform == "win32" and 'utf-8' or 'koi8-r'
        _reload()
        self.failUnlessReallyEqual(get_io_encoding(), expected)

        mock_stdout.encoding = 'nonexistent_encoding'
        if sys.platform == "win32":
            _reload()
            self.failUnlessReallyEqual(get_io_encoding(), 'utf-8')
        else:
            self.failUnlessRaises(AssertionError, _reload)

    def test_get_io_encoding_not_from_stdout(self):
        preferredencoding = 'koi8-r'
        def call_locale_getpreferredencoding():
            return preferredencoding
        self.patch(locale, 'getpreferredencoding', call_locale_getpreferredencoding)
        mock_stdout = MockStdout()
        self.patch(sys, 'stdout', mock_stdout)

        expected = sys.platform == "win32" and 'utf-8' or 'koi8-r'
        _reload()
        self.failUnlessReallyEqual(get_io_encoding(), expected)

        mock_stdout.encoding = None
        _reload()
        self.failUnlessReallyEqual(get_io_encoding(), expected)

        preferredencoding = None
        _reload()
        self.failUnlessReallyEqual(get_io_encoding(), 'utf-8')

    def test_argv_to_unicode(self):
        encodingutil.io_encoding = 'utf-8'
        self.failUnlessRaises(usage.UsageError,
                              argv_to_unicode,
                              lumiere_nfc.encode('latin1'))

    def test_unicode_to_output(self):
        encodingutil.io_encoding = 'koi8-r'
        self.failUnlessRaises(UnicodeEncodeError, unicode_to_output, lumiere_nfc)

    def test_no_unicode_normalization(self):
        # Pretend to run on a Unicode platform.
        # listdir_unicode normalized to NFC in 1.7beta, but now doesn't.

        def call_os_listdir(path):
            return [Artonwall_nfd]
        self.patch(os, 'listdir', call_os_listdir)
        self.patch(sys, 'platform', 'darwin')

        _reload()
        self.failUnlessReallyEqual(listdir_unicode(u'/dummy'), [Artonwall_nfd])


# The following tests apply only to platforms that don't store filenames as
# Unicode entities on the filesystem.
class EncodingUtilNonUnicodePlatform(unittest.TestCase):
    def setUp(self):
        # Mock sys.platform because unicode_platform() uses it
        self.original_platform = sys.platform
        sys.platform = 'linux'

    def tearDown(self):
        sys.platform = self.original_platform
        _reload()

    def test_listdir_unicode(self):
        # What happens if latin1-encoded filenames are encountered on an UTF-8
        # filesystem?
        def call_os_listdir(path):
            return [
              lumiere_nfc.encode('utf-8'),
              lumiere_nfc.encode('latin1')
            ]
        self.patch(os, 'listdir', call_os_listdir)

        sys_filesystemencoding = 'utf-8'
        def call_sys_getfilesystemencoding():
            return sys_filesystemencoding
        self.patch(sys, 'getfilesystemencoding', call_sys_getfilesystemencoding)

        _reload()
        self.failUnlessRaises(FilenameEncodingError,
                              listdir_unicode,
                              u'/dummy')

        # We're trying to list a directory whose name cannot be represented in
        # the filesystem encoding.  This should fail.
        sys_filesystemencoding = 'ascii'
        _reload()
        self.failUnlessRaises(FilenameEncodingError,
                              listdir_unicode,
                              u'/' + lumiere_nfc)


class EncodingUtil(ReallyEqualMixin):
    def setUp(self):
        self.original_platform = sys.platform
        sys.platform = self.platform

    def tearDown(self):
        sys.platform = self.original_platform
        _reload()

    def test_argv_to_unicode(self):
        if 'argv' not in dir(self):
            return

        mock_stdout = MockStdout()
        mock_stdout.encoding = self.io_encoding
        self.patch(sys, 'stdout', mock_stdout)

        argu = lumiere_nfc
        argv = self.argv
        _reload()
        self.failUnlessReallyEqual(argv_to_unicode(argv), argu)

    def test_unicode_to_url(self):
        self.failUnless(unicode_to_url(lumiere_nfc), "lumi\xc3\xa8re")

    def test_unicode_to_output(self):
        if 'argv' not in dir(self):
            return

        mock_stdout = MockStdout()
        mock_stdout.encoding = self.io_encoding
        self.patch(sys, 'stdout', mock_stdout)

        _reload()
        self.failUnlessReallyEqual(unicode_to_output(lumiere_nfc), self.argv)

    def test_unicode_platform(self):
        matrix = {
          'linux2': False,
          'linux3': False,
          'openbsd4': False,
          'win32':  True,
          'darwin': True,
        }

        _reload()
        self.failUnlessReallyEqual(unicode_platform(), matrix[self.platform])

    def test_listdir_unicode(self):
        if 'dirlist' not in dir(self):
            return

        try:
            u"test".encode(self.filesystem_encoding)
        except (LookupError, AttributeError):
            raise unittest.SkipTest("This platform does not support the '%s' filesystem encoding "
                                    "that we are testing for the benefit of a different platform."
                                    % (self.filesystem_encoding,))

        def call_os_listdir(path):
            return self.dirlist
        self.patch(os, 'listdir', call_os_listdir)

        def call_sys_getfilesystemencoding():
            return self.filesystem_encoding
        self.patch(sys, 'getfilesystemencoding', call_sys_getfilesystemencoding)

        _reload()
        filenames = listdir_unicode(u'/dummy')

        self.failUnlessEqual(set([normalize(fname) for fname in filenames]),
                             set(TEST_FILENAMES))


class StdlibUnicode(unittest.TestCase):
    """This mainly tests that some of the stdlib functions support Unicode paths, but also that
    listdir_unicode works for valid filenames."""

    def test_mkdir_open_exists_abspath_listdir_expanduser(self):
        skip_if_cannot_represent_filename(lumiere_nfc)

        try:
            os.mkdir(lumiere_nfc)
        except EnvironmentError as e:
            raise unittest.SkipTest("%r\nIt is possible that the filesystem on which this test is being run "
                                    "does not support Unicode, even though the platform does." % (e,))

        fn = lumiere_nfc + u'/' + lumiere_nfc + u'.txt'
        open(fn, 'wb').close()
        self.failUnless(os.path.exists(fn))
        self.failUnless(os.path.exists(os.path.join(os.getcwdu(), fn)))
        filenames = listdir_unicode(lumiere_nfc)

        # We only require that the listing includes a filename that is canonically equivalent
        # to lumiere_nfc (on Mac OS X, it will be the NFD equivalent).
        self.failUnlessIn(lumiere_nfc + ".txt", set([normalize(fname) for fname in filenames]))

        expanded = fileutil.expanduser(u"~/" + lumiere_nfc)
        self.failIfIn(u"~", expanded)
        self.failUnless(expanded.endswith(lumiere_nfc), expanded)

    def test_open_unrepresentable(self):
        if unicode_platform():
            raise unittest.SkipTest("This test is not applicable to platforms that represent filenames as Unicode.")

        enc = get_filesystem_encoding()
        fn = u'\u2621.txt'
        try:
            fn.encode(enc)
            raise unittest.SkipTest("This test cannot be run unless we know a filename that is not representable.")
        except UnicodeEncodeError:
            self.failUnlessRaises(UnicodeEncodeError, open, fn, 'wb')


class QuoteOutput(ReallyEqualMixin, unittest.TestCase):
    def tearDown(self):
        _reload()

    def _check(self, inp, out, enc, optional_quotes, quote_newlines):
        out2 = out
        if optional_quotes:
            out2 = out2[1:-1]
        self.failUnlessReallyEqual(quote_output(inp, encoding=enc, quote_newlines=quote_newlines), out)
        self.failUnlessReallyEqual(quote_output(inp, encoding=enc, quotemarks=False, quote_newlines=quote_newlines), out2)
        if out[0:2] == 'b"':
            pass
        elif isinstance(inp, str):
            self.failUnlessReallyEqual(quote_output(unicode(inp), encoding=enc, quote_newlines=quote_newlines), out)
            self.failUnlessReallyEqual(quote_output(unicode(inp), encoding=enc, quotemarks=False, quote_newlines=quote_newlines), out2)
        else:
            self.failUnlessReallyEqual(quote_output(inp.encode('utf-8'), encoding=enc, quote_newlines=quote_newlines), out)
            self.failUnlessReallyEqual(quote_output(inp.encode('utf-8'), encoding=enc, quotemarks=False, quote_newlines=quote_newlines), out2)

    def _test_quote_output_all(self, enc):
        def check(inp, out, optional_quotes=False, quote_newlines=None):
            self._check(inp, out, enc, optional_quotes, quote_newlines)

        # optional single quotes
        check("foo",  "'foo'",  True)
        check("\\",   "'\\'",   True)
        check("$\"`", "'$\"`'", True)
        check("\n",   "'\n'",   True, quote_newlines=False)

        # mandatory single quotes
        check("\"",   "'\"'")

        # double quotes
        check("'",    "\"'\"")
        check("\n",   "\"\\x0a\"", quote_newlines=True)
        check("\x00", "\"\\x00\"")

        # invalid Unicode and astral planes
        check(u"\uFDD0\uFDEF",       "\"\\ufdd0\\ufdef\"")
        check(u"\uDC00\uD800",       "\"\\udc00\\ud800\"")
        check(u"\uDC00\uD800\uDC00", "\"\\udc00\\U00010000\"")
        check(u"\uD800\uDC00",       "\"\\U00010000\"")
        check(u"\uD800\uDC01",       "\"\\U00010001\"")
        check(u"\uD801\uDC00",       "\"\\U00010400\"")
        check(u"\uDBFF\uDFFF",       "\"\\U0010ffff\"")
        check(u"'\uDBFF\uDFFF",      "\"'\\U0010ffff\"")
        check(u"\"\uDBFF\uDFFF",     "\"\\\"\\U0010ffff\"")

        # invalid UTF-8
        check("\xFF",                "b\"\\xff\"")
        check("\x00\"$\\`\x80\xFF",  "b\"\\x00\\\"\\$\\\\\\`\\x80\\xff\"")

    def test_quote_output_ascii(self, enc='ascii'):
        def check(inp, out, optional_quotes=False, quote_newlines=None):
            self._check(inp, out, enc, optional_quotes, quote_newlines)

        self._test_quote_output_all(enc)
        check(u"\u00D7",   "\"\\xd7\"")
        check(u"'\u00D7",  "\"'\\xd7\"")
        check(u"\"\u00D7", "\"\\\"\\xd7\"")
        check(u"\u2621",   "\"\\u2621\"")
        check(u"'\u2621",  "\"'\\u2621\"")
        check(u"\"\u2621", "\"\\\"\\u2621\"")
        check(u"\n",       "'\n'",      True, quote_newlines=False)
        check(u"\n",       "\"\\x0a\"", quote_newlines=True)

    def test_quote_output_latin1(self, enc='latin1'):
        def check(inp, out, optional_quotes=False, quote_newlines=None):
            self._check(inp, out.encode('latin1'), enc, optional_quotes, quote_newlines)

        self._test_quote_output_all(enc)
        check(u"\u00D7",   u"'\u00D7'", True)
        check(u"'\u00D7",  u"\"'\u00D7\"")
        check(u"\"\u00D7", u"'\"\u00D7'")
        check(u"\u00D7\"", u"'\u00D7\"'", True)
        check(u"\u2621",   u"\"\\u2621\"")
        check(u"'\u2621",  u"\"'\\u2621\"")
        check(u"\"\u2621", u"\"\\\"\\u2621\"")
        check(u"\n",       u"'\n'", True, quote_newlines=False)
        check(u"\n",       u"\"\\x0a\"", quote_newlines=True)

    def test_quote_output_utf8(self, enc='utf-8'):
        def check(inp, out, optional_quotes=False, quote_newlines=None):
            self._check(inp, out.encode('utf-8'), enc, optional_quotes, quote_newlines)

        self._test_quote_output_all(enc)
        check(u"\u2621",   u"'\u2621'", True)
        check(u"'\u2621",  u"\"'\u2621\"")
        check(u"\"\u2621", u"'\"\u2621'")
        check(u"\u2621\"", u"'\u2621\"'", True)
        check(u"\n",       u"'\n'", True, quote_newlines=False)
        check(u"\n",       u"\"\\x0a\"", quote_newlines=True)

    def test_quote_output_default(self):
        self.patch(encodingutil, 'io_encoding', 'ascii')
        self.test_quote_output_ascii(None)

        self.patch(encodingutil, 'io_encoding', 'latin1')
        self.test_quote_output_latin1(None)

        self.patch(encodingutil, 'io_encoding', 'utf-8')
        self.test_quote_output_utf8(None)


def win32_other(win32, other):
    return win32 if sys.platform == "win32" else other

class QuotePaths(ReallyEqualMixin, unittest.TestCase):
    def test_quote_path(self):
        self.failUnlessReallyEqual(quote_path([u'foo', u'bar']), "'foo/bar'")
        self.failUnlessReallyEqual(quote_path([u'foo', u'bar'], quotemarks=True), "'foo/bar'")
        self.failUnlessReallyEqual(quote_path([u'foo', u'bar'], quotemarks=False), "foo/bar")
        self.failUnlessReallyEqual(quote_path([u'foo', u'\nbar']), '"foo/\\x0abar"')
        self.failUnlessReallyEqual(quote_path([u'foo', u'\nbar'], quotemarks=True), '"foo/\\x0abar"')
        self.failUnlessReallyEqual(quote_path([u'foo', u'\nbar'], quotemarks=False), '"foo/\\x0abar"')

        self.failUnlessReallyEqual(quote_local_unicode_path(u"\\\\?\\C:\\foo"),
                                   win32_other("'C:\\foo'", "'\\\\?\\C:\\foo'"))
        self.failUnlessReallyEqual(quote_local_unicode_path(u"\\\\?\\C:\\foo", quotemarks=True),
                                   win32_other("'C:\\foo'", "'\\\\?\\C:\\foo'"))
        self.failUnlessReallyEqual(quote_local_unicode_path(u"\\\\?\\C:\\foo", quotemarks=False),
                                   win32_other("C:\\foo", "\\\\?\\C:\\foo"))
        self.failUnlessReallyEqual(quote_local_unicode_path(u"\\\\?\\UNC\\foo\\bar"),
                                   win32_other("'\\\\foo\\bar'", "'\\\\?\\UNC\\foo\\bar'"))
        self.failUnlessReallyEqual(quote_local_unicode_path(u"\\\\?\\UNC\\foo\\bar", quotemarks=True),
                                   win32_other("'\\\\foo\\bar'", "'\\\\?\\UNC\\foo\\bar'"))
        self.failUnlessReallyEqual(quote_local_unicode_path(u"\\\\?\\UNC\\foo\\bar", quotemarks=False),
                                   win32_other("\\\\foo\\bar", "\\\\?\\UNC\\foo\\bar"))

    def test_quote_filepath(self):
        foo_bar_fp = FilePath(win32_other(u'C:\\foo\\bar', u'/foo/bar'))
        self.failUnlessReallyEqual(quote_filepath(foo_bar_fp),
                                   win32_other("'C:\\foo\\bar'", "'/foo/bar'"))
        self.failUnlessReallyEqual(quote_filepath(foo_bar_fp, quotemarks=True),
                                   win32_other("'C:\\foo\\bar'", "'/foo/bar'"))
        self.failUnlessReallyEqual(quote_filepath(foo_bar_fp, quotemarks=False),
                                   win32_other("C:\\foo\\bar", "/foo/bar"))

        if sys.platform == "win32":
            foo_longfp = FilePath(u'\\\\?\\C:\\foo')
            self.failUnlessReallyEqual(quote_filepath(foo_longfp),
                                       "'C:\\foo'")
            self.failUnlessReallyEqual(quote_filepath(foo_longfp, quotemarks=True),
                                       "'C:\\foo'")
            self.failUnlessReallyEqual(quote_filepath(foo_longfp, quotemarks=False),
                                       "C:\\foo")


class FilePaths(ReallyEqualMixin, unittest.TestCase):
    def test_to_filepath(self):
        foo_u = win32_other(u'C:\\foo', u'/foo')

        nosep_fp = to_filepath(foo_u)
        sep_fp = to_filepath(foo_u + os.path.sep)

        for fp in (nosep_fp, sep_fp):
            self.failUnlessReallyEqual(fp, FilePath(foo_u))
            if encodingutil.use_unicode_filepath:
                self.failUnlessReallyEqual(fp.path, foo_u)

        if sys.platform == "win32":
            long_u = u'\\\\?\\C:\\foo'
            longfp = to_filepath(long_u + u'\\')
            self.failUnlessReallyEqual(longfp, FilePath(long_u))
            self.failUnlessReallyEqual(longfp.path, long_u)

    def test_extend_filepath(self):
        foo_bfp = FilePath(win32_other(b'C:\\foo', b'/foo'))
        foo_ufp = FilePath(win32_other(u'C:\\foo', u'/foo'))
        foo_bar_baz_u = win32_other(u'C:\\foo\\bar\\baz', u'/foo/bar/baz')

        for foo_fp in (foo_bfp, foo_ufp):
            fp = extend_filepath(foo_fp, [u'bar', u'baz'])
            self.failUnlessReallyEqual(fp, FilePath(foo_bar_baz_u))
            if encodingutil.use_unicode_filepath:
                self.failUnlessReallyEqual(fp.path, foo_bar_baz_u)

    def test_unicode_from_filepath(self):
        foo_bfp = FilePath(win32_other(b'C:\\foo', b'/foo'))
        foo_ufp = FilePath(win32_other(u'C:\\foo', u'/foo'))
        foo_u = win32_other(u'C:\\foo', u'/foo')

        for foo_fp in (foo_bfp, foo_ufp):
            self.failUnlessReallyEqual(unicode_from_filepath(foo_fp), foo_u)

    def test_unicode_segments_from(self):
        foo_bfp = FilePath(win32_other(b'C:\\foo', b'/foo'))
        foo_ufp = FilePath(win32_other(u'C:\\foo', u'/foo'))
        foo_bar_baz_bfp = FilePath(win32_other(b'C:\\foo\\bar\\baz', b'/foo/bar/baz'))
        foo_bar_baz_ufp = FilePath(win32_other(u'C:\\foo\\bar\\baz', u'/foo/bar/baz'))

        for foo_fp in (foo_bfp, foo_ufp):
            for foo_bar_baz_fp in (foo_bar_baz_bfp, foo_bar_baz_ufp):
                self.failUnlessReallyEqual(unicode_segments_from(foo_bar_baz_fp, foo_fp),
                                           [u'bar', u'baz'])


class UbuntuKarmicUTF8(EncodingUtil, unittest.TestCase):
    uname = 'Linux korn 2.6.31-14-generic #48-Ubuntu SMP Fri Oct 16 14:05:01 UTC 2009 x86_64'
    argv = 'lumi\xc3\xa8re'
    platform = 'linux2'
    filesystem_encoding = 'UTF-8'
    io_encoding = 'UTF-8'
    dirlist = ['test_file', '\xc3\x84rtonwall.mp3', 'Blah blah.txt']

class UbuntuKarmicLatin1(EncodingUtil, unittest.TestCase):
    uname = 'Linux korn 2.6.31-14-generic #48-Ubuntu SMP Fri Oct 16 14:05:01 UTC 2009 x86_64'
    argv = 'lumi\xe8re'
    platform = 'linux2'
    filesystem_encoding = 'ISO-8859-1'
    io_encoding = 'ISO-8859-1'
    dirlist = ['test_file', 'Blah blah.txt', '\xc4rtonwall.mp3']

class Windows(EncodingUtil, unittest.TestCase):
    uname = 'Windows XP 5.1.2600 x86 x86 Family 15 Model 75 Step ping 2, AuthenticAMD'
    argv = 'lumi\xc3\xa8re'
    platform = 'win32'
    filesystem_encoding = 'mbcs'
    io_encoding = 'utf-8'
    dirlist = [u'Blah blah.txt', u'test_file', u'\xc4rtonwall.mp3']

class MacOSXLeopard(EncodingUtil, unittest.TestCase):
    uname = 'Darwin g5.local 9.8.0 Darwin Kernel Version 9.8.0: Wed Jul 15 16:57:01 PDT 2009; root:xnu-1228.15.4~1/RELEASE_PPC Power Macintosh powerpc'
    output = 'lumi\xc3\xa8re'
    platform = 'darwin'
    filesystem_encoding = 'utf-8'
    io_encoding = 'UTF-8'
    dirlist = [u'A\u0308rtonwall.mp3', u'Blah blah.txt', u'test_file']

class MacOSXLeopard7bit(EncodingUtil, unittest.TestCase):
    uname = 'Darwin g5.local 9.8.0 Darwin Kernel Version 9.8.0: Wed Jul 15 16:57:01 PDT 2009; root:xnu-1228.15.4~1/RELEASE_PPC Power Macintosh powerpc'
    platform = 'darwin'
    filesystem_encoding = 'utf-8'
    io_encoding = 'US-ASCII'
    dirlist = [u'A\u0308rtonwall.mp3', u'Blah blah.txt', u'test_file']

class OpenBSD(EncodingUtil, unittest.TestCase):
    uname = 'OpenBSD 4.1 GENERIC#187 i386 Intel(R) Celeron(R) CPU 2.80GHz ("GenuineIntel" 686-class)'
    platform = 'openbsd4'
    filesystem_encoding = '646'
    io_encoding = '646'
    # Oops, I cannot write filenames containing non-ascii characters


class TestToFromStr(ReallyEqualMixin, unittest.TestCase):
    def test_to_str(self):
        self.failUnlessReallyEqual(to_str("foo"), "foo")
        self.failUnlessReallyEqual(to_str("lumi\xc3\xa8re"), "lumi\xc3\xa8re")
        self.failUnlessReallyEqual(to_str("\xFF"), "\xFF")  # passes through invalid UTF-8 -- is this what we want?
        self.failUnlessReallyEqual(to_str(u"lumi\u00E8re"), "lumi\xc3\xa8re")
        self.failUnlessReallyEqual(to_str(None), None)

    def test_from_utf8_or_none(self):
        self.failUnlessRaises(AssertionError, from_utf8_or_none, u"foo")
        self.failUnlessReallyEqual(from_utf8_or_none("lumi\xc3\xa8re"), u"lumi\u00E8re")
        self.failUnlessReallyEqual(from_utf8_or_none(None), None)
        self.failUnlessRaises(UnicodeDecodeError, from_utf8_or_none, "\xFF")
