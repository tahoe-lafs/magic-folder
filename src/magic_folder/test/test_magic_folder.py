from __future__ import print_function

import os, time
from errno import ENOENT

from twisted.internet import reactor
from twisted.python.runtime import platform
from twisted.python.filepath import FilePath

from eliot import (
    Message,
    start_action,
)

from allmydata.util.encodingutil import to_filepath

from eliot.twisted import (
    inline_callbacks,
)

from magic_folder.util.eliotutil import (
    log_call_deferred,
)

from ..util import (
    fake_inotify,
)

if platform.isMacOSX():
    def modified_mtime_barrier(path):
        """
        macOS filesystem (HFS+) has one second resolution on filesystem
        modification time metadata.  Make sure that code running after this
        function which modifies the file will produce a changed mtime on that
        file.
        """
        try:
            mtime = path.getModificationTime()
        except OSError as e:
            if e.errno == ENOENT:
                # If the file does not exist yet, there is no current mtime
                # value that might match a future mtime value.  We have
                # nothing to do.
                return
            # Propagate any other errors as we don't know what's going on.
            raise
        if int(time.time()) == int(mtime):
            # The current time matches the file's modification time, to the
            # resolution of the filesystem metadata.  Therefore, change the
            # current time.
            time.sleep(1)
else:
    def modified_mtime_barrier(path):
        """
        non-macOS platforms have sufficiently high-resolution file modification
        time metadata that nothing in particular is required to ensure a
        modified mtime as a result of a future write.
        """


@inline_callbacks
def notify_when_pending(uploader, filename):
    with start_action(action_type=u"notify-when-pending", filename=filename):
        relpath = uploader._get_relpath(FilePath(filename))
        while not uploader.is_pending(relpath):
            Message.log(message_type=u"not-pending")
            yield uploader.set_hook('inotify')


class FileOperationsHelper(object):
    """
    This abstracts all file operations we might do in magic-folder unit-tests.

    This is so we can correctly wait for inotify events to 'actually'
    propagate. For the mock tests this is easy, since we're sending
    them sychronously. For the Real tests we have to wait for the
    actual inotify thing.
    """
    _timeout = 30.0

    def __init__(self, uploader, inject_events=False):
        self._uploader = uploader
        self._inotify = fake_inotify  # fixme?
        self._fake_inotify = inject_events

    @log_call_deferred(action_type=u"fileops:move")
    def move(self, from_path_u, to_path_u):
        from_fname = from_path_u
        to_fname = to_path_u
        d = self._uploader.set_hook('inotify')
        os.rename(from_fname, to_fname)

        self._maybe_notify(to_fname, self._inotify.IN_MOVED_TO)
        # hmm? we weren't faking IN_MOVED_FROM previously .. but seems like we should have been?
        # self._uploader._notifier.event(to_filepath(from_fname), self._inotify.IN_MOVED_FROM)
        return d.addTimeout(self._timeout, reactor)

    @log_call_deferred(action_type=u"fileops:write")
    def write(self, path_u, contents):
        fname = path_u
        if not os.path.exists(fname):
            self._maybe_notify(fname, self._inotify.IN_CREATE)

        d = notify_when_pending(self._uploader, path_u)

        modified_mtime_barrier(FilePath(fname))
        with open(fname, "wb") as f:
            f.write(contents)

        self._maybe_notify(fname, self._inotify.IN_CLOSE_WRITE)
        return d.addTimeout(self._timeout, reactor)

    @log_call_deferred(action_type=u"fileops:mkdir")
    def mkdir(self, path_u):
        fname = path_u
        d = self._uploader.set_hook('inotify')
        os.mkdir(fname)
        self._maybe_notify(fname, self._inotify.IN_CREATE | self._inotify.IN_ISDIR)
        return d.addTimeout(self._timeout, reactor)

    @log_call_deferred(action_type=u"fileops:delete")
    def delete(self, path_u):
        fname = path_u
        d = self._uploader.set_hook('inotify')
        if os.path.isdir(fname):
            remove = os.rmdir
        else:
            remove = os.unlink
        remove(fname)

        self._maybe_notify(fname, self._inotify.IN_DELETE)
        return d.addTimeout(self._timeout, reactor)

    def _maybe_notify(self, fname, mask):
        if self._fake_inotify:
            self._uploader._notifier.event(to_filepath(fname), mask)
