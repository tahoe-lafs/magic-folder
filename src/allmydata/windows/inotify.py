
# Windows near-equivalent to twisted.internet.inotify
# This should only be imported on Windows.

from __future__ import print_function

import six
import os, sys

from eliot import (
    start_action,
    Message,
    log_call,
)

from twisted.internet import reactor
from twisted.internet.threads import deferToThread

from allmydata.util.fake_inotify import humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED
[humanReadableMask, \
    IN_WATCH_MASK, IN_ACCESS, IN_MODIFY, IN_ATTRIB, IN_CLOSE_NOWRITE, IN_CLOSE_WRITE, \
    IN_OPEN, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE, IN_DELETE_SELF, \
    IN_MOVE_SELF, IN_UNMOUNT, IN_Q_OVERFLOW, IN_IGNORED, IN_ONLYDIR, IN_DONT_FOLLOW, \
    IN_MASK_ADD, IN_ISDIR, IN_ONESHOT, IN_CLOSE, IN_MOVED, IN_CHANGED]

from allmydata.util.assertutil import _assert, precondition
from allmydata.util.encodingutil import quote_output
from allmydata.util import log, fileutil
from allmydata.util.pollmixin import PollMixin
from ..util.eliotutil import (
    MAYBE_NOTIFY,
    CALLBACK,
)

from ctypes import WINFUNCTYPE, WinError, windll, POINTER, byref, create_string_buffer, \
    addressof, get_last_error
from ctypes.wintypes import BOOL, HANDLE, DWORD, LPCWSTR, LPVOID

if six.PY3:
    long = int

# <http://msdn.microsoft.com/en-us/library/gg258116%28v=vs.85%29.aspx>
FILE_LIST_DIRECTORY              = 1

# <http://msdn.microsoft.com/en-us/library/aa363858%28v=vs.85%29.aspx>
CreateFileW = WINFUNCTYPE(
    HANDLE,  LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE,
    use_last_error=True
)(("CreateFileW", windll.kernel32))

FILE_SHARE_READ                  = 0x00000001
FILE_SHARE_WRITE                 = 0x00000002
FILE_SHARE_DELETE                = 0x00000004

OPEN_EXISTING                    = 3

FILE_FLAG_BACKUP_SEMANTICS       = 0x02000000

# <http://msdn.microsoft.com/en-us/library/ms724211%28v=vs.85%29.aspx>
CloseHandle = WINFUNCTYPE(
    BOOL,  HANDLE,
    use_last_error=True
)(("CloseHandle", windll.kernel32))

# <http://msdn.microsoft.com/en-us/library/aa365465%28v=vs.85%29.aspx>
ReadDirectoryChangesW = WINFUNCTYPE(
    BOOL,  HANDLE, LPVOID, DWORD, BOOL, DWORD, POINTER(DWORD), LPVOID, LPVOID,
    use_last_error=True
)(("ReadDirectoryChangesW", windll.kernel32))

FILE_NOTIFY_CHANGE_FILE_NAME     = 0x00000001
FILE_NOTIFY_CHANGE_DIR_NAME      = 0x00000002
FILE_NOTIFY_CHANGE_ATTRIBUTES    = 0x00000004
#FILE_NOTIFY_CHANGE_SIZE         = 0x00000008
FILE_NOTIFY_CHANGE_LAST_WRITE    = 0x00000010
FILE_NOTIFY_CHANGE_LAST_ACCESS   = 0x00000020
#FILE_NOTIFY_CHANGE_CREATION     = 0x00000040
FILE_NOTIFY_CHANGE_SECURITY      = 0x00000100

# <http://msdn.microsoft.com/en-us/library/aa364391%28v=vs.85%29.aspx>
FILE_ACTION_ADDED                = 0x00000001
FILE_ACTION_REMOVED              = 0x00000002
FILE_ACTION_MODIFIED             = 0x00000003
FILE_ACTION_RENAMED_OLD_NAME     = 0x00000004
FILE_ACTION_RENAMED_NEW_NAME     = 0x00000005

_action_to_string = {
    FILE_ACTION_ADDED            : "FILE_ACTION_ADDED",
    FILE_ACTION_REMOVED          : "FILE_ACTION_REMOVED",
    FILE_ACTION_MODIFIED         : "FILE_ACTION_MODIFIED",
    FILE_ACTION_RENAMED_OLD_NAME : "FILE_ACTION_RENAMED_OLD_NAME",
    FILE_ACTION_RENAMED_NEW_NAME : "FILE_ACTION_RENAMED_NEW_NAME",
}

_action_to_inotify_mask = {
    FILE_ACTION_ADDED            : IN_CREATE,
    FILE_ACTION_REMOVED          : IN_DELETE,
    FILE_ACTION_MODIFIED         : IN_CHANGED,
    FILE_ACTION_RENAMED_OLD_NAME : IN_MOVED_FROM,
    FILE_ACTION_RENAMED_NEW_NAME : IN_MOVED_TO,
}

INVALID_HANDLE_VALUE             = 0xFFFFFFFF

FALSE = 0
TRUE = 1

class Event(object):
    """
    * action:   a FILE_ACTION_* constant (not a bit mask)
    * filename: a Unicode string, giving the name relative to the watched directory
    """
    def __init__(self, action, filename):
        self.action = action
        self.filename = filename

    def __repr__(self):
        return "Event(%r, %r)" % (_action_to_string.get(self.action, self.action), self.filename)


class FileNotifyInformation(object):
    """
    I represent a buffer containing FILE_NOTIFY_INFORMATION structures, and can
    iterate over those structures, decoding them into Event objects.
    """

    def __init__(self, size=1024):
        self.size = size
        self.buffer = create_string_buffer(size)
        address = addressof(self.buffer)
        _assert(address & 3 == 0, "address 0x%X returned by create_string_buffer is not DWORD-aligned" % (address,))
        self.data = None

    def read_changes(self, hDirectory, recursive, filter):
        bytes_returned = DWORD(0)
        r = ReadDirectoryChangesW(hDirectory,
                                  self.buffer,
                                  self.size,
                                  recursive,
                                  filter,
                                  byref(bytes_returned),
                                  None,  # NULL -> no overlapped I/O
                                  None   # NULL -> no completion routine
                                 )
        if r == 0:
            self.data = None
            raise WinError(get_last_error())
        self.data = self.buffer.raw[:bytes_returned.value]

    def __iter__(self):
        # Iterator implemented as generator: <http://docs.python.org/library/stdtypes.html#generator-types>
        if self.data is None:
            return
        pos = 0
        while True:
            bytes = self._read_dword(pos+8)
            s = Event(self._read_dword(pos+4),
                      self.data[pos+12 : pos+12+bytes].decode('utf-16-le'))
            Message.log(message_type="fni", info=repr(s))

            next_entry_offset = self._read_dword(pos)
            yield s
            if next_entry_offset == 0:
                break
            pos = pos + next_entry_offset

    def _read_dword(self, i):
        # little-endian
        return ( ord(self.data[i])          |
                (ord(self.data[i+1]) <<  8) |
                (ord(self.data[i+2]) << 16) |
                (ord(self.data[i+3]) << 24))


def _open_directory(path_u):
    hDirectory = CreateFileW(path_u,
                             FILE_LIST_DIRECTORY,         # access rights
                             FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                                          # don't prevent other processes from accessing
                             None,                        # no security descriptor
                             OPEN_EXISTING,               # directory must already exist
                             FILE_FLAG_BACKUP_SEMANTICS,  # necessary to open a directory
                             None                         # no template file
                            )
    if hDirectory == INVALID_HANDLE_VALUE:
        e = WinError(get_last_error())
        raise OSError("Opening directory %s gave WinError: %s" % (quote_output(path_u), e))
    return hDirectory


def simple_test():
    path_u = u"test"
    filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE
    recursive = TRUE

    hDirectory = _open_directory(path_u)
    fni = FileNotifyInformation()
    print("Waiting...")
    while True:
        fni.read_changes(hDirectory, recursive, filter)
        print(repr(fni.data))
        for info in fni:
            print(info)

def medium_test():
    from twisted.python.filepath import FilePath

    def print_(*event):
        print(event)

    notifier = INotify()
    notifier.set_pending_delay(1.0)
    IN_EXCL_UNLINK = long(0x04000000)
    mask = (  IN_CREATE
            | IN_CLOSE_WRITE
            | IN_MOVED_TO
            | IN_MOVED_FROM
            | IN_DELETE
            | IN_ONLYDIR
            | IN_EXCL_UNLINK
    )
    notifier.watch(FilePath(u"test"), mask, callbacks=[print_], recursive=True)
    notifier.startReading()
    reactor.run()


NOT_STARTED = "NOT_STARTED"
STARTED     = "STARTED"
STOPPING    = "STOPPING"
STOPPED     = "STOPPED"

class INotify(PollMixin):
    def __init__(self):
        self._state = NOT_STARTED
        self._filter = None
        self._callbacks = None
        self._hDirectory = None
        self._path = None
        self._pending = set()
        self._pending_delay = 1.0
        self._pending_call = None
        self.recursive_includes_new_subdirectories = True

    def set_pending_delay(self, delay):
        self._pending_delay = delay

    def startReading(self):
        deferToThread(self._thread)
        return self.poll(lambda: self._state != NOT_STARTED)

    def stopReading(self):
        # FIXME race conditions
        if self._state != STOPPED:
            self._state = STOPPING
        if self._pending_call:
            self._pending_call.cancel()
            self._pending_call = None

    def wait_until_stopped(self):
        try:
            fileutil.write(os.path.join(self._path.path, u".ignore-me"), "")
        except IOError:
            pass
        return self.poll(lambda: self._state == STOPPED)

    def watch(self, path, mask=IN_WATCH_MASK, autoAdd=False, callbacks=None, recursive=False):
        precondition(self._state == NOT_STARTED, "watch() can only be called before startReading()", state=self._state)
        precondition(self._filter is None, "only one watch is supported")
        precondition(isinstance(autoAdd, bool), autoAdd=autoAdd)
        precondition(isinstance(recursive, bool), recursive=recursive)
        #precondition(autoAdd == recursive, "need autoAdd and recursive to be the same", autoAdd=autoAdd, recursive=recursive)

        self._path = path
        path_u = path.path
        if not isinstance(path_u, unicode):
            path_u = path_u.decode(sys.getfilesystemencoding())
            _assert(isinstance(path_u, unicode), path_u=path_u)

        self._filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE

        if mask & (IN_ACCESS | IN_CLOSE_NOWRITE | IN_OPEN):
            self._filter = self._filter | FILE_NOTIFY_CHANGE_LAST_ACCESS
        if mask & IN_ATTRIB:
            self._filter = self._filter | FILE_NOTIFY_CHANGE_ATTRIBUTES | FILE_NOTIFY_CHANGE_SECURITY

        self._recursive = TRUE if recursive else FALSE
        self._callbacks = callbacks or []
        self._hDirectory = _open_directory(path_u)

    def _thread(self):
        try:
            _assert(self._filter is not None, "no watch set")

            # To call Twisted or Tahoe APIs, use reactor.callFromThread as described in
            # <http://twistedmatrix.com/documents/current/core/howto/threading.html>.

            fni = FileNotifyInformation()

            while True:
                self._state = STARTED
                action = start_action(
                    action_type=u"read-changes",
                    directory=self._path.path,
                    recursive=self._recursive,
                    filter=self._filter,
                )
                try:
                    with action:
                        fni.read_changes(self._hDirectory, self._recursive, self._filter)
                except WindowsError as e:
                    self._state = STOPPING

                if self._check_stop():
                    return
                for info in fni:
                    path = self._path.preauthChild(info.filename)  # FilePath with Unicode path
                    if info.action == FILE_ACTION_MODIFIED and path.isdir():
                        Message.log(
                            message_type=u"filtering-out",
                            info=repr(info),
                        )
                        continue
                    else:
                        Message.log(
                            message_type=u"processing",
                            info=repr(info),
                        )
                    #mask = _action_to_inotify_mask.get(info.action, IN_CHANGED)

                    @log_call(
                        action_type=MAYBE_NOTIFY.action_type,
                        include_args=[],
                        include_result=False,
                    )
                    def _do_pending_calls():
                        event_mask = IN_CHANGED
                        self._pending_call = None
                        for path1 in self._pending:
                            if self._callbacks:
                                for cb in self._callbacks:
                                    try:
                                        with CALLBACK(inotify_events=event_mask):
                                            cb(None, path1, event_mask)
                                    except Exception as e2:
                                        log.err(e2)
                        self._pending = set()

                    def _maybe_notify(path2):
                        if path2 not in self._pending:
                            self._pending.add(path2)
                        if self._state not in [STOPPING, STOPPED]:
                            _do_pending_calls()
#                        if self._pending_call is None and self._state not in [STOPPING, STOPPED]:
#                            self._pending_call = reactor.callLater(self._pending_delay, _do_pending_calls)

                    reactor.callFromThread(_maybe_notify, path)
                    if self._check_stop():
                        return
        except Exception as e:
            log.err(e)
            self._state = STOPPED
            raise

    def _check_stop(self):
        if self._state == STOPPING:
            hDirectory = self._hDirectory
            self._callbacks = None
            self._hDirectory = None
            CloseHandle(hDirectory)
            self._state = STOPPED
            if self._pending_call:
                self._pending_call.cancel()
                self._pending_call = None

        return self._state == STOPPED
