from __future__ import print_function

done = False

def initialize():
    global done
    import sys
    if sys.platform != "win32" or done:
        return True
    done = True

    import codecs, re
    from ctypes import WINFUNCTYPE, WinError, windll, POINTER, byref, c_int, get_last_error
    from ctypes.wintypes import BOOL, HANDLE, DWORD, UINT, LPWSTR, LPCWSTR, LPVOID

    from allmydata.util import log
    from allmydata.util.encodingutil import canonical_encoding

    # <https://msdn.microsoft.com/en-us/library/ms680621%28VS.85%29.aspx>
    SetErrorMode = WINFUNCTYPE(
        UINT,  UINT,
        use_last_error=True
    )(("SetErrorMode", windll.kernel32))

    SEM_FAILCRITICALERRORS = 0x0001
    SEM_NOOPENFILEERRORBOX = 0x8000

    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX)

    original_stderr = sys.stderr

    # If any exception occurs in this code, we'll probably try to print it on stderr,
    # which makes for frustrating debugging if stderr is directed to our wrapper.
    # So be paranoid about catching errors and reporting them to original_stderr,
    # so that we can at least see them.
    def _complain(message):
        print(isinstance(message, str) and message or repr(message), file=original_stderr)
        log.msg(message, level=log.WEIRD)

    # Work around <http://bugs.python.org/issue6058>.
    codecs.register(lambda name: name == 'cp65001' and codecs.lookup('utf-8') or None)

    # Make Unicode console output work independently of the current code page.
    # This also fixes <http://bugs.python.org/issue1602>.
    # Credit to Michael Kaplan <https://blogs.msdn.com/b/michkap/archive/2010/04/07/9989346.aspx>
    # and TZOmegaTZIOY
    # <http://stackoverflow.com/questions/878972/windows-cmd-encoding-change-causes-python-crash/1432462#1432462>.
    try:
        # <https://msdn.microsoft.com/en-us/library/ms683231(VS.85).aspx>
        # HANDLE WINAPI GetStdHandle(DWORD nStdHandle);
        # returns INVALID_HANDLE_VALUE, NULL, or a valid handle
        #
        # <https://msdn.microsoft.com/en-us/library/aa364960(VS.85).aspx>
        # DWORD WINAPI GetFileType(DWORD hFile);
        #
        # <https://msdn.microsoft.com/en-us/library/ms683167(VS.85).aspx>
        # BOOL WINAPI GetConsoleMode(HANDLE hConsole, LPDWORD lpMode);

        GetStdHandle = WINFUNCTYPE(
            HANDLE,  DWORD,
            use_last_error=True
        )(("GetStdHandle", windll.kernel32))

        STD_OUTPUT_HANDLE = DWORD(-11)
        STD_ERROR_HANDLE  = DWORD(-12)

        GetFileType = WINFUNCTYPE(
            DWORD,  DWORD,
            use_last_error=True
        )(("GetFileType", windll.kernel32))

        FILE_TYPE_CHAR   = 0x0002
        FILE_TYPE_REMOTE = 0x8000

        GetConsoleMode = WINFUNCTYPE(
            BOOL,  HANDLE, POINTER(DWORD),
            use_last_error=True
        )(("GetConsoleMode", windll.kernel32))

        INVALID_HANDLE_VALUE = DWORD(-1).value

        def not_a_console(handle):
            if handle == INVALID_HANDLE_VALUE or handle is None:
                return True
            return ((GetFileType(handle) & ~FILE_TYPE_REMOTE) != FILE_TYPE_CHAR
                    or GetConsoleMode(handle, byref(DWORD())) == 0)

        old_stdout_fileno = None
        old_stderr_fileno = None
        if hasattr(sys.stdout, 'fileno'):
            old_stdout_fileno = sys.stdout.fileno()
        if hasattr(sys.stderr, 'fileno'):
            old_stderr_fileno = sys.stderr.fileno()

        STDOUT_FILENO = 1
        STDERR_FILENO = 2
        real_stdout = (old_stdout_fileno == STDOUT_FILENO)
        real_stderr = (old_stderr_fileno == STDERR_FILENO)

        if real_stdout:
            hStdout = GetStdHandle(STD_OUTPUT_HANDLE)
            if not_a_console(hStdout):
                real_stdout = False

        if real_stderr:
            hStderr = GetStdHandle(STD_ERROR_HANDLE)
            if not_a_console(hStderr):
                real_stderr = False

        if real_stdout or real_stderr:
            # <https://msdn.microsoft.com/en-us/library/windows/desktop/ms687401%28v=vs.85%29.aspx>
            # BOOL WINAPI WriteConsoleW(HANDLE hOutput, LPWSTR lpBuffer, DWORD nChars,
            #                           LPDWORD lpCharsWritten, LPVOID lpReserved);

            WriteConsoleW = WINFUNCTYPE(
                BOOL,  HANDLE, LPWSTR, DWORD, POINTER(DWORD), LPVOID,
                use_last_error=True
            )(("WriteConsoleW", windll.kernel32))

            class UnicodeOutput(object):
                def __init__(self, hConsole, stream, fileno, name):
                    self._hConsole = hConsole
                    self._stream = stream
                    self._fileno = fileno
                    self.closed = False
                    self.softspace = False
                    self.mode = 'w'
                    self.encoding = 'utf-8'
                    self.name = name
                    if hasattr(stream, 'encoding') and canonical_encoding(stream.encoding) != 'utf-8':
                        log.msg("%s: %r had encoding %r, but we're going to write UTF-8 to it" %
                                (name, stream, stream.encoding), level=log.CURIOUS)
                    self.flush()

                def isatty(self):
                    return False
                def close(self):
                    # don't really close the handle, that would only cause problems
                    self.closed = True
                def fileno(self):
                    return self._fileno
                def flush(self):
                    if self._hConsole is None:
                        try:
                            self._stream.flush()
                        except Exception as e:
                            _complain("%s.flush: %r from %r" % (self.name, e, self._stream))
                            raise

                def write(self, text):
                    try:
                        if self._hConsole is None:
                            if isinstance(text, unicode):
                                text = text.encode('utf-8')
                            self._stream.write(text)
                        else:
                            if not isinstance(text, unicode):
                                text = str(text).decode('utf-8')
                            remaining = len(text)
                            while remaining > 0:
                                n = DWORD(0)
                                # There is a shorter-than-documented limitation on the length of the string
                                # passed to WriteConsoleW (see #1232).
                                retval = WriteConsoleW(self._hConsole, text, min(remaining, 10000), byref(n), None)
                                if retval == 0:
                                    raise IOError("WriteConsoleW failed with WinError: %s" % (WinError(get_last_error()),))
                                if n.value == 0:
                                    raise IOError("WriteConsoleW returned %r, n.value = 0" % (retval,))
                                remaining -= n.value
                                if remaining == 0: break
                                text = text[n.value:]
                    except Exception as e:
                        _complain("%s.write: %r" % (self.name, e))
                        raise

                def writelines(self, lines):
                    try:
                        for line in lines:
                            self.write(line)
                    except Exception as e:
                        _complain("%s.writelines: %r" % (self.name, e))
                        raise

            if real_stdout:
                sys.stdout = UnicodeOutput(hStdout, None, STDOUT_FILENO, '<Unicode console stdout>')
            else:
                sys.stdout = UnicodeOutput(None, sys.stdout, old_stdout_fileno, '<Unicode redirected stdout>')

            if real_stderr:
                sys.stderr = UnicodeOutput(hStderr, None, STDERR_FILENO, '<Unicode console stderr>')
            else:
                sys.stderr = UnicodeOutput(None, sys.stderr, old_stderr_fileno, '<Unicode redirected stderr>')
    except Exception as e:
        _complain("exception %r while fixing up sys.stdout and sys.stderr" % (e,))

    # This works around <http://bugs.python.org/issue2128>.

    # <https://msdn.microsoft.com/en-us/library/windows/desktop/ms683156%28v=vs.85%29.aspx>
    GetCommandLineW = WINFUNCTYPE(
        LPWSTR,
        use_last_error=True
    )(("GetCommandLineW", windll.kernel32))

    # <https://msdn.microsoft.com/en-us/library/windows/desktop/bb776391%28v=vs.85%29.aspx>
    CommandLineToArgvW = WINFUNCTYPE(
        POINTER(LPWSTR),  LPCWSTR, POINTER(c_int),
        use_last_error=True
    )(("CommandLineToArgvW", windll.shell32))

    argc = c_int(0)
    argv_unicode = CommandLineToArgvW(GetCommandLineW(), byref(argc))
    if argv_unicode is None:
        raise WinError(get_last_error())

    # Because of <http://bugs.python.org/issue8775> (and similar limitations in
    # twisted), the 'bin/tahoe' script cannot invoke us with the actual Unicode arguments.
    # Instead it "mangles" or escapes them using \x7F as an escape character, which we
    # unescape here.
    def unmangle(s):
        return re.sub(u'\\x7F[0-9a-fA-F]*\\;', lambda m: unichr(int(m.group(0)[1:-1], 16)), s)

    try:
        argv = [unmangle(argv_unicode[i]).encode('utf-8') for i in xrange(0, argc.value)]
    except Exception as e:
        _complain("%s:  could not unmangle Unicode arguments.\n%r"
                  % (sys.argv[0], [argv_unicode[i] for i in xrange(0, argc.value)]))
        raise

    # Take only the suffix with the same number of arguments as sys.argv.
    # This accounts for anything that can cause initial arguments to be stripped,
    # for example, the Python interpreter or any options passed to it, or runner
    # scripts such as 'coverage run'. It works even if there are no such arguments,
    # as in the case of a frozen executable created by bb-freeze or similar.

    sys.argv = argv[-len(sys.argv):]
    if sys.argv[0].endswith('.pyscript'):
        sys.argv[0] = sys.argv[0][:-9]
