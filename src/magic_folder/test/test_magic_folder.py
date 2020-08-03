from __future__ import print_function

import os, sys, time
import stat
from os.path import join, isdir
from errno import ENOENT

from twisted.internet import defer, reactor
from twisted.python.runtime import platform
from twisted.python.filepath import FilePath

from eliot import (
    Message,
    start_action,
)
from allmydata.util.assertutil import precondition

from allmydata.util import yamlutil
from allmydata.util.encodingutil import to_filepath
from allmydata.util.consumer import download_to_data

from allmydata.util.fileutil import abspath_expanduser_unicode

from eliot.twisted import (
    inline_callbacks,
)

from magic_folder.util.eliotutil import (
    log_call_deferred,
)

from magic_folder.magic_folder import (
    ConfigurationError,
    get_inotify_module,
    load_magic_folders,
)
from ..util import (
    fake_inotify,
)

from .. import (
    magicpath,
)

from .common import (
    SyncTestCase,
)
_debug = False

try:
    get_inotify_module()
except NotImplementedError:
    support_missing = True
    support_message = (
        "Magic Folder support can only be tested for-real on an OS that "
        "supports inotify or equivalent."
    )
else:
    support_missing = False
    support_message = None

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


class NewConfigUtilTests(SyncTestCase):

    def setUp(self):
        # some tests look at the umask of created directories or files
        # so we set an explicit one
        old_umask = os.umask(0o022)
        self.addCleanup(lambda: os.umask(old_umask))
        self.basedir = abspath_expanduser_unicode(unicode(self.mktemp()))
        os.mkdir(self.basedir)
        self.local_dir = abspath_expanduser_unicode(unicode(self.mktemp()))
        os.mkdir(self.local_dir)
        privdir = join(self.basedir, "private")
        os.mkdir(privdir)

        self.poll_interval = 60
        self.collective_dircap = u"a" * 32
        self.magic_folder_dircap = u"b" * 32

        self.folders = {
            u"default": {
                u"directory": self.local_dir,
                u"upload_dircap": self.magic_folder_dircap,
                u"collective_dircap": self.collective_dircap,
                u"poll_interval": self.poll_interval,
            }
        }

        # we need a bit of tahoe.cfg
        self.write_tahoe_config(
            self.basedir,
            u"[magic_folder]\n"
            u"enabled = True\n",
        )
        # ..and the yaml
        self.write_magic_folder_config(self.basedir, self.folders)
        return super(NewConfigUtilTests, self).setUp()

    def write_tahoe_config(self, basedir, tahoe_config):
        with open(join(basedir, u"tahoe.cfg"), "w") as f:
            f.write(tahoe_config)

    def write_magic_folder_config(self, basedir, folder_configuration):
        yaml_fname = join(basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write(yamlutil.safe_dump({u"magic-folders": folder_configuration}))

    def test_load(self):
        folders = load_magic_folders(self.basedir)
        self.assertEqual(['default'], list(folders.keys()))
        self.assertEqual(folders['default'][u'umask'], 0o077)

    def test_load_makes_directory(self):
        """
        If the *directory* does not exist then it is created by
        ``load_magic_folders``.
        """
        os.rmdir(self.local_dir)
        # Just pick some arbitrary bits.
        # rwxr-xr--
        perm = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH
        self.folders[u"default"][u"umask"] = (0o777 & ~perm)
        self.write_magic_folder_config(self.basedir, self.folders)

        load_magic_folders(self.basedir)

        # It is created.
        self.assertTrue(
            isdir(self.local_dir),
            "magic-folder local directory {} was not created".format(
                self.local_dir,
            ),
        )
        # It has permissions determined by the configured umask.
        if sys.platform != "win32":
            self.assertEqual(
                perm,
                stat.S_IMODE(os.stat(self.local_dir).st_mode),
            )
        else:
            # Do directories even have permissions on Windows?
            print("Not asserting directory-creation mode on windows")

    def test_directory_collision(self):
        """
        If a non-directory already exists at the magic folder's configured local
        directory path, ``load_magic_folders`` raises an exception.
        """
        os.rmdir(self.local_dir)
        open(self.local_dir, "w").close()

        with self.assertRaises(ConfigurationError) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "exists and is not a directory",
            str(ctx.exception),
        )

    def test_directory_creation_error(self):
        """
        If a directory at the magic folder's configured local directory path
        cannot be created for some other reason, ``load_magic_folders`` raises
        an exception.
        """
        os.rmdir(self.local_dir)
        open(self.local_dir, "w").close()
        self.folders[u"default"][u"directory"] = self.local_dir + "/foo"
        self.write_magic_folder_config(self.basedir, self.folders)

        with self.assertRaises(ConfigurationError) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "could not be created",
            str(ctx.exception),
        )

    def test_both_styles_of_config(self):
        os.unlink(join(self.basedir, u"private", u"magic_folders.yaml"))
        with self.assertRaises(Exception) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "[magic_folder] is enabled but has no YAML file and no 'local.directory' option",
            str(ctx.exception)
        )

    def test_wrong_obj(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write('----\n')

        with self.assertRaises(Exception) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "should contain a dict",
            str(ctx.exception)
        )

    def test_no_magic_folders(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write('')

        with self.assertRaises(Exception) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "should contain a dict",
            str(ctx.exception)
        )

    def test_magic_folders_not_dict(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write('magic-folders: "foo"\n')

        with self.assertRaises(Exception) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "should be a dict",
            str(ctx.exception)
        )
        self.assertIn(
            "'magic-folders'",
            str(ctx.exception)
        )

    def test_wrong_umask_obj(self):
        """
        If a umask is given for a magic-folder that is not an integer, an
        exception is raised.
        """
        self.folders[u"default"][u"umask"] = "0077"
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write(yamlutil.safe_dump({u"magic-folders": self.folders}))

        with self.assertRaises(Exception) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "umask must be an integer",
            str(ctx.exception)
        )

    def test_wrong_sub_obj(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write("magic-folders:\n  default:   foo\n")

        with self.assertRaises(Exception) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "must itself be a dict",
            str(ctx.exception)
        )

    def test_missing_interval(self):
        del self.folders[u"default"]["poll_interval"]
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write(yamlutil.safe_dump({u"magic-folders": self.folders}))

        with self.assertRaises(Exception) as ctx:
            load_magic_folders(self.basedir)
        self.assertIn(
            "missing 'poll_interval'",
            str(ctx.exception)
        )


def iterate_downloader(magic):
    return magic.downloader._processing_iteration()


def iterate_uploader(magic):
    return magic.uploader._processing_iteration()

@inline_callbacks
def iterate(magic):
    yield iterate_uploader(magic)
    yield iterate_downloader(magic)


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


class CheckerMixin(object):
    """
    Factored out of one of the many test classes.

    *Ideally* these should just be bare helper methods, but many of
    them already depended upon self.* state. One major problem is that
    they're using self.magicfolder *but* some of the alice/bob tests
    use this, too, and they just do "self.magicfolder =
    self.bob_magicfolder" or whatever before calling them, which is
    *horrible*.
    """
    def _check_mkdir(self, name_u):
        return self._check_file(name_u + u"/", "", directory=True)

    @defer.inlineCallbacks
    def _check_file(self, name_u, data, temporary=False, directory=False):
        precondition(not (temporary and directory), temporary=temporary, directory=directory)

        # print "%r._check_file(%r, %r, temporary=%r, directory=%r)" % (self, name_u, data, temporary, directory)
        previously_uploaded = self._get_count('uploader.objects_succeeded')
        previously_disappeared = self._get_count('uploader.objects_disappeared')

        path_u = abspath_expanduser_unicode(name_u, base=self.local_dir)

        if directory:
            yield self.fileops.mkdir(path_u)
        else:
            # We don't use FilePath.setContent() here because it creates a temporary file that
            # is renamed into place, which causes events that the test is not expecting.
            yield self.fileops.write(path_u, data)
            yield iterate(self.magicfolder)
            if temporary:
                yield iterate(self.magicfolder)
                yield self.fileops.delete(path_u)

        yield iterate(self.magicfolder)
        encoded_name_u = magicpath.path2magic(name_u)

        yield self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0)
        if temporary:
            yield self.failUnlessReallyEqual(self._get_count('uploader.objects_disappeared'),
                                             previously_disappeared + 1)
        else:
            yield self.magicfolder.uploader._upload_dirnode.list()
            x = yield self.magicfolder.uploader._upload_dirnode.get(encoded_name_u)
            actual_data = yield download_to_data(x)
            self.failUnlessReallyEqual(actual_data, data)
            self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'),
                                       previously_uploaded + 1)

        self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0)

    @defer.inlineCallbacks
    def _check_version_in_dmd(self, magicfolder, relpath_u, expected_version):
        encoded_name_u = magicpath.path2magic(relpath_u)
        result = yield magicfolder.downloader._get_collective_latest_file(encoded_name_u)
        self.assertIsNot(
            result,
            None,
            "collective_latest_file({}) is None".format(encoded_name_u),
        )
        node, metadata = result
        self.assertIsNot(
            metadata,
            None,
            "collective_latest_file({}) metadata is None".format(encoded_name_u),
        )
        self.failUnlessEqual(metadata['version'], expected_version)

    def _check_version_in_local_db(self, magicfolder, relpath_u, expected_version):
        db_entry = magicfolder._db.get_db_entry(relpath_u)
        if db_entry is not None:
            #print "_check_version_in_local_db: %r has version %s" % (relpath_u, version)
            self.failUnlessEqual(db_entry.version, expected_version)

    def _check_file_gone(self, magicfolder, relpath_u):
        path = os.path.join(magicfolder.uploader._local_path_u, relpath_u)
        self.assertTrue(not os.path.exists(path))

    def _check_uploader_count(self, name, expected, magic=None):
        if magic is None:
            magic = self.alice_magicfolder
        self.failUnlessReallyEqual(
            self._get_count(
                'uploader.'+name,
                client=magic._client,
            ),
            expected,
            "Pending: {}\n"
            "Deque:   {}\n".format(magic.uploader._pending, magic.uploader._deque),
        )

    def _check_downloader_count(self, name, expected, magic=None):
        self.failUnlessReallyEqual(self._get_count('downloader.'+name, client=(magic or self.bob_magicfolder)._client),
                                   expected)

    def _get_count(self, name, client=None):
        counters = (client or self.get_client()).stats_provider.get_stats()["counters"]
        return counters.get('magic_folder.%s' % (name,), 0)
