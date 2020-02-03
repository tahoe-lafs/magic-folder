from __future__ import print_function

import os, sys, time
import stat, shutil, json
import mock
from os.path import join, exists, isdir
from errno import ENOENT

from twisted.internet import defer, task, reactor
from twisted.python.runtime import platform
from twisted.python.filepath import FilePath

from testtools.matchers import (
    Not,
    Is,
    ContainsDict,
    Equals,
)

from eliot import (
    Message,
    start_action,
    log_call,
)
from eliot.twisted import DeferredContext

from allmydata.interfaces import (
    IDirectoryNode,
    NoSharesError,
)
from allmydata.util.assertutil import precondition

from allmydata.util import fake_inotify, fileutil, configutil, yamlutil
from allmydata.util.encodingutil import get_filesystem_encoding, to_filepath
from allmydata.util.consumer import download_to_data
from allmydata.test.no_network import GridTestMixin
from allmydata.test.common_util import ReallyEqualMixin
from .common import (
    ShouldFailMixin,
    SyncTestCase,
    AsyncTestCase,
    skipIf,
)
from .cli.test_magic_folder import MagicFolderCLITestMixin

from allmydata.frontends import magic_folder
from allmydata.frontends.magic_folder import (
    MagicFolder, WriteFileMixin,
    ConfigurationError,
)
from allmydata import magicfolderdb, magicpath
from allmydata.util.fileutil import get_pathinfo
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.immutable.upload import Data
from allmydata.mutable.common import (
        UnrecoverableFileError,
)

from ..util.eliotutil import (
    inline_callbacks,
    log_call_deferred,
)

_debug = False

try:
    magic_folder.get_inotify_module()
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
        folders = magic_folder.load_magic_folders(self.basedir)
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

        magic_folder.load_magic_folders(self.basedir)

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
            magic_folder.load_magic_folders(self.basedir)
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
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "could not be created",
            str(ctx.exception),
        )

    def test_both_styles_of_config(self):
        os.unlink(join(self.basedir, u"private", u"magic_folders.yaml"))
        with self.assertRaises(Exception) as ctx:
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "[magic_folder] is enabled but has no YAML file and no 'local.directory' option",
            str(ctx.exception)
        )

    def test_wrong_obj(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write('----\n')

        with self.assertRaises(Exception) as ctx:
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "should contain a dict",
            str(ctx.exception)
        )

    def test_no_magic_folders(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write('')

        with self.assertRaises(Exception) as ctx:
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "should contain a dict",
            str(ctx.exception)
        )

    def test_magic_folders_not_dict(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write('magic-folders: "foo"\n')

        with self.assertRaises(Exception) as ctx:
            magic_folder.load_magic_folders(self.basedir)
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
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "umask must be an integer",
            str(ctx.exception)
        )

    def test_wrong_sub_obj(self):
        yaml_fname = join(self.basedir, u"private", u"magic_folders.yaml")
        with open(yaml_fname, "w") as f:
            f.write("magic-folders:\n  default:   foo\n")

        with self.assertRaises(Exception) as ctx:
            magic_folder.load_magic_folders(self.basedir)
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
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "missing 'poll_interval'",
            str(ctx.exception)
        )


class LegacyConfigUtilTests(SyncTestCase):

    def setUp(self):
        # create a valid 'old style' magic-folder configuration
        self.basedir = abspath_expanduser_unicode(unicode(self.mktemp()))
        os.mkdir(self.basedir)
        self.local_dir = abspath_expanduser_unicode(unicode(self.mktemp()))
        os.mkdir(self.local_dir)
        privdir = join(self.basedir, "private")
        os.mkdir(privdir)

        # state tests might need to know
        self.poll_interval = 60
        self.collective_dircap = u"a" * 32
        self.magic_folder_dircap = u"b" * 32

        # write fake config structure
        with open(join(self.basedir, u"tahoe.cfg"), "w") as f:
            f.write(
                u"[magic_folder]\n"
                u"enabled = True\n"
                u"local.directory = {}\n"
                u"poll_interval = {}\n".format(
                    self.local_dir,
                    self.poll_interval,
                )
            )
        with open(join(privdir, "collective_dircap"), "w") as f:
            f.write("{}\n".format(self.collective_dircap))
        with open(join(privdir, "magic_folder_dircap"), "w") as f:
            f.write("{}\n".format(self.magic_folder_dircap))
        with open(join(privdir, "magicfolderdb.sqlite"), "w") as f:
            pass
        return super(LegacyConfigUtilTests, self).setUp()

    def test_load_legacy_no_dir(self):
        expected = self.local_dir + 'foo'
        with open(join(self.basedir, u"tahoe.cfg"), "w") as f:
            f.write(
                u"[magic_folder]\n"
                u"enabled = True\n"
                u"local.directory = {}\n"
                u"poll_interval = {}\n".format(
                    expected,
                    self.poll_interval,
                )
            )

        magic_folder.load_magic_folders(self.basedir)

        self.assertTrue(
            isdir(expected),
            "magic-folder local directory {} was not created".format(
                expected,
            ),
        )

    def test_load_legacy_not_a_dir(self):
        with open(join(self.basedir, u"tahoe.cfg"), "w") as f:
            f.write(
                u"[magic_folder]\n"
                u"enabled = True\n"
                u"local.directory = {}\n"
                u"poll_interval = {}\n".format(
                    self.local_dir + "foo",
                    self.poll_interval,
                )
            )
        with open(self.local_dir + "foo", "w") as f:
            f.write("not a directory")

        with self.assertRaises(ConfigurationError) as ctx:
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "is not a directory",
            str(ctx.exception)
        )

    def test_load_legacy_and_new(self):
        with open(join(self.basedir, u"private", u"magic_folders.yaml"), "w") as f:
            f.write("---")

        with self.assertRaises(Exception) as ctx:
            magic_folder.load_magic_folders(self.basedir)
        self.assertIn(
            "both old-style configuration and new-style",
            str(ctx.exception)
        )

    def test_upgrade(self):
        # test data is created in setUp; upgrade config
        magic_folder._upgrade_magic_folder_config(self.basedir)

        # ensure old stuff is gone
        self.assertFalse(
            exists(join(self.basedir, "private", "collective_dircap"))
        )
        self.assertFalse(
            exists(join(self.basedir, "private", "magic_folder_dircap"))
        )
        self.assertFalse(
            exists(join(self.basedir, "private", "magicfolderdb.sqlite"))
        )

        # ensure we've got the new stuff
        self.assertTrue(
            exists(join(self.basedir, "private", "magicfolder_default.sqlite"))
        )
        # what about config?
        config = configutil.get_config(join(self.basedir, u"tahoe.cfg"))
        self.assertFalse(config.has_option("magic_folder", "local.directory"))

    def test_load_legacy(self):
        folders = magic_folder.load_magic_folders(self.basedir)

        self.assertEqual(['default'], list(folders.keys()))
        self.assertTrue(
            exists(join(self.basedir, "private", "collective_dircap"))
        )
        self.assertTrue(
            exists(join(self.basedir, "private", "magic_folder_dircap"))
        )
        self.assertTrue(
            exists(join(self.basedir, "private", "magicfolderdb.sqlite"))
        )

    def test_load_legacy_upgrade(self):
        magic_folder.maybe_upgrade_magic_folders(self.basedir)
        folders = magic_folder.load_magic_folders(self.basedir)

        self.assertEqual(['default'], list(folders.keys()))
        # 'legacy' files should be gone
        self.assertFalse(
            exists(join(self.basedir, "private", "collective_dircap"))
        )
        self.assertFalse(
            exists(join(self.basedir, "private", "magic_folder_dircap"))
        )
        self.assertFalse(
            exists(join(self.basedir, "private", "magicfolderdb.sqlite"))
        )



class MagicFolderDbTests(SyncTestCase):

    def setUp(self):
        self.temp = abspath_expanduser_unicode(unicode(self.mktemp()))
        os.mkdir(self.temp)
        self.addCleanup(lambda: shutil.rmtree(self.temp))
        dbfile = abspath_expanduser_unicode(u"testdb.sqlite", base=self.temp)
        self.db = magicfolderdb.get_magicfolderdb(dbfile, create_version=(magicfolderdb.SCHEMA_v1, 1))
        self.addCleanup(lambda: self.db.close())
        self.failUnless(self.db, "unable to create magicfolderdb from %r" % (dbfile,))
        self.failUnlessEqual(self.db.VERSION, 1)
        return super(MagicFolderDbTests, self).setUp()

    def test_create(self):
        self.db.did_upload_version(
            relpath_u=u'fake_path',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri='URI:foo',
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )

        entry = self.db.get_db_entry(u'fake_path')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, 'URI:foo')

    def test_update(self):
        self.db.did_upload_version(
            relpath_u=u'fake_path',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri='URI:foo',
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )
        self.db.did_upload_version(
            relpath_u=u'fake_path',
            version=1,
            last_uploaded_uri=None,
            last_downloaded_uri='URI:bar',
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )

        entry = self.db.get_db_entry(u'fake_path')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, 'URI:bar')
        self.assertEqual(entry.version, 1)

    def test_same_content_different_path(self):
        content_uri = 'URI:CHK:27d2yruqwk6zb2w7hkbbfxxbue:ipmszjysmn4vdeaxz7rtxtv3gwv6vrqcg2ktrdmn4oxqqucltxxq:2:4:1052835840'
        self.db.did_upload_version(
            relpath_u=u'path0',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri=content_uri,
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )
        self.db.did_upload_version(
            relpath_u=u'path1',
            version=0,
            last_uploaded_uri=None,
            last_downloaded_uri=content_uri,
            last_downloaded_timestamp=1234.5,
            pathinfo=get_pathinfo(self.temp),  # a directory, but should be fine for test
        )

        entry = self.db.get_db_entry(u'path0')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, content_uri)

        entry = self.db.get_db_entry(u'path1')
        self.assertTrue(entry is not None)
        self.assertEqual(entry.last_downloaded_uri, content_uri)

    def test_get_direct_children(self):
        """
        ``get_direct_children`` returns a list of ``PathEntry`` representing each
        local file in the database which is a direct child of the given path.
        """
        def add_file(relpath_u):
            self.db.did_upload_version(
                relpath_u=relpath_u,
                version=0,
                last_uploaded_uri=None,
                last_downloaded_uri=None,
                last_downloaded_timestamp=1234,
                pathinfo=get_pathinfo(self.temp),
            )
        paths = [
            u"some_random_file",
            u"the_target_directory_is_elsewhere",
            u"the_target_directory_is_not_this/",
            u"the_target_directory_is_not_this/and_not_in_here",
            u"the_target_directory/",
            u"the_target_directory/foo",
            u"the_target_directory/bar",
            u"the_target_directory/baz",
            u"the_target_directory/quux/",
            u"the_target_directory/quux/exclude_grandchildren",
            u"the_target_directory/quux/and_great_grandchildren/",
            u"the_target_directory/quux/and_great_grandchildren/foo",
            u"the_target_directory_is_over/stuff",
            u"please_ignore_this_for_sure",
        ]
        for relpath_u in paths:
            add_file(relpath_u)

        expected_paths = [
            u"the_target_directory/foo",
            u"the_target_directory/bar",
            u"the_target_directory/baz",
            u"the_target_directory/quux/",
        ]

        actual_paths = list(
            localpath.relpath_u
            for localpath
            in self.db.get_direct_children(u"the_target_directory")
        )
        self.assertEqual(expected_paths, actual_paths)


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



class MagicFolderAliceBobTestMixin(MagicFolderCLITestMixin, ShouldFailMixin, ReallyEqualMixin, CheckerMixin):
    inject_inotify = False

    def setUp(self):
        MagicFolderCLITestMixin.setUp(self)
        temp = self.mktemp()
        self.basedir = abspath_expanduser_unicode(temp.decode(get_filesystem_encoding()))
        # set_up_grid depends on self.basedir existing
        with start_action(action_type=u"set_up_grid"):
            self.set_up_grid(num_clients=2, oneshare=True)

        self.alice_clock = task.Clock()
        self.bob_clock = task.Clock()

        # this is all just .setup_alice_and_bob(), essentially
        self.alice_magicfolder = None
        self.bob_magicfolder = None

        self.alice_magic_dir = abspath_expanduser_unicode(u"Alice-magic", base=self.basedir)
        self.mkdir_nonascii(self.alice_magic_dir)
        self.bob_magic_dir = abspath_expanduser_unicode(u"Bob-magic", base=self.basedir)
        self.mkdir_nonascii(self.bob_magic_dir)

        # Alice creates a Magic Folder, invites herself and joins.
        d = DeferredContext(self.do_create_magic_folder(0))
        d.addCallback(lambda ign: self.do_invite(0, self.alice_nickname))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(0, self.alice_magic_dir, self.invite_code))
        def get_alice_caps(ign):
            self.alice_collective_dircap, self.alice_upload_dircap = self.get_caps_from_files(0)
        d.addCallback(get_alice_caps)
        d.addCallback(lambda ign: self.check_joined_config(0, self.alice_upload_dircap))
        d.addCallback(lambda ign: self.check_config(0, self.alice_magic_dir))
        def get_Alice_magicfolder(result):
            self.alice_magicfolder = self.init_magicfolder(0, self.alice_upload_dircap,
                                                           self.alice_collective_dircap,
                                                           self.alice_magic_dir, self.alice_clock)
            self.alice_fileops = FileOperationsHelper(self.alice_magicfolder.uploader, self.inject_inotify)
            d0 = self.alice_magicfolder.uploader.set_hook('iteration')
            d1 = self.alice_magicfolder.downloader.set_hook('iteration')
            self.alice_clock.advance(self.alice_magicfolder.uploader._pending_delay + 1)
            d0.addCallback(lambda ign: d1)
            d0.addCallback(lambda ign: result)
            return d0
        d.addCallback(get_Alice_magicfolder)

        # Alice invites Bob. Bob joins.
        d.addCallback(lambda ign: self.do_invite(0, self.bob_nickname))
        def get_invite_code(result):
            self.invite_code = result[1].strip()
        d.addCallback(get_invite_code)
        d.addCallback(lambda ign: self.do_join(1, self.bob_magic_dir, self.invite_code))
        def get_bob_caps(ign):
            self.bob_collective_dircap, self.bob_upload_dircap = self.get_caps_from_files(1)
        d.addCallback(get_bob_caps)
        d.addCallback(lambda ign: self.check_joined_config(1, self.bob_upload_dircap))
        d.addCallback(lambda ign: self.check_config(1, self.bob_magic_dir))
        def get_Bob_magicfolder(result):
            self.bob_magicfolder = self.init_magicfolder(1, self.bob_upload_dircap,
                                                         self.bob_collective_dircap,
                                                         self.bob_magic_dir, self.bob_clock)
            self.bob_fileops = FileOperationsHelper(self.bob_magicfolder.uploader, self.inject_inotify)
            d0 = self.bob_magicfolder.uploader.set_hook('iteration')
            d1 = self.bob_magicfolder.downloader.set_hook('iteration')
            self.bob_clock.advance(self.alice_magicfolder.uploader._pending_delay + 1)
            d0.addCallback(lambda ign: d1)
            d0.addCallback(lambda ign: result)
            return d0
        d.addCallback(get_Bob_magicfolder)
        return d.result

    @defer.inlineCallbacks
    def tearDown(self):
        yield GridTestMixin.tearDown(self)

        for mf in [self.alice_magicfolder, self.bob_magicfolder]:
            mf.uploader._clock.advance(mf.uploader._pending_delay + 1)
            mf.downloader._clock.advance(mf.downloader._poll_interval + 1)

    @inline_callbacks
    def test_alice_delete_bob_restore(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')

        with start_action(action_type=u"alice:create"):
            yield self.alice_fileops.write(alice_fname, 'contents0\n')
            yield iterate(self.alice_magicfolder)  # for windows

        with start_action(action_type=u"alice:upload"):
            yield iterate_uploader(self.alice_magicfolder)
            yield alice_proc

        with start_action(action_type=u"alice:check-upload"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)

        with start_action(action_type=u"bob:download"):
            yield iterate_downloader(self.bob_magicfolder)

        with start_action(action_type=u"alice:recheck-upload"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)

        with start_action(action_type=u"bob:check-download"):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
            yield self.failUnlessReallyEqual(
                self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
                0
            )
            yield self.failUnlessReallyEqual(
                self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
                1
            )

            yield iterate(self.bob_magicfolder)  # for windows


        bob_proc = self.bob_magicfolder.uploader.set_hook('processed')
        alice_proc = self.alice_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"bob:delete"):
            yield self.bob_fileops.delete(bob_fname)
            yield iterate(self.bob_magicfolder)  # for windows

        with start_action(action_type=u"bob:upload"):
            yield iterate_uploader(self.bob_magicfolder)
            yield bob_proc

        with start_action(action_type=u"alice:download"):
            yield iterate_downloader(self.alice_magicfolder)
            yield alice_proc

        # check versions
        with start_action(action_type=u"bob:check-upload"):
            node, metadata = yield self.alice_magicfolder.downloader._get_collective_latest_file(u'blam')
            self.assertTrue(metadata['deleted'])
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)

        with start_action(action_type=u"alice:check-download"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

        with start_action(action_type=u"alice:mysterious-iterate"):
            # not *entirely* sure why we need to iterate Alice for the
            # real test here. But, we do.
            yield iterate(self.alice_magicfolder)

        # now alice restores it (alice should upload, bob download)
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"alice:rewrite"):
            yield self.alice_fileops.write(alice_fname, 'new contents\n')
            yield iterate(self.alice_magicfolder)  # for windows

        with start_action(action_type=u"alice:reupload"):
            yield iterate_uploader(self.alice_magicfolder)
            yield alice_proc

        with start_action(action_type=u"bob:redownload"):
            yield iterate_downloader(self.bob_magicfolder)
            yield bob_proc

        # check versions
        with start_action(action_type=u"bob:recheck-download"):
            node, metadata = yield self.alice_magicfolder.downloader._get_collective_latest_file(u'blam')
            self.assertTrue('deleted' not in metadata or not metadata['deleted'])
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 2)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 2)

        with start_action(action_type=u"alice:final-check-upload"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 2)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 2)

    @inline_callbacks
    def test_alice_sees_bobs_delete_with_error(self):
        # alice creates a file, bob deletes it -- and we also arrange
        # for Alice's file to have "gone missing" as well.
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # alice creates a file, bob downloads it
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"alice:create"):
            yield self.alice_fileops.write(alice_fname, 'contents0\n')
            yield iterate(self.alice_magicfolder)  # for windows

        with start_action(action_type=u"alice:upload"):
            yield iterate_uploader(self.alice_magicfolder)
            yield alice_proc  # alice uploads

        with start_action(action_type=u"bob:download"):
            yield iterate_downloader(self.bob_magicfolder)
            yield bob_proc    # bob downloads

        with start_action(action_type=u"mysterious:iterate"):
            yield iterate(self.alice_magicfolder)  # for windows
            yield iterate(self.bob_magicfolder)  # for windows

        # check the state (XXX I had to switch the versions to 0; is that really right? why?)
        with start_action(action_type=u"alice:check"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)

        with start_action(action_type=u"bob:check"):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
            self.failUnlessReallyEqual(
                self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
                0
            )
            self.failUnlessReallyEqual(
                self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
                1
            )

        bob_proc = self.bob_magicfolder.uploader.set_hook('processed')
        alice_proc = self.alice_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"bob:delete"):
            yield self.bob_fileops.delete(bob_fname)

        with start_action(action_type=u"alice:delete"):
            # just after notifying bob, we also delete alice's,
            # covering the 'except' flow in _rename_deleted_file()
            yield self.alice_fileops.delete(alice_fname)

        with start_action(action_type=u"bob:upload-delete"):
            yield iterate_uploader(self.bob_magicfolder)
            yield bob_proc

        with start_action(action_type=u"alice:download-delete"):
            yield iterate_downloader(self.alice_magicfolder)
            yield alice_proc

        # check versions
        with start_action(action_type=u"bob:check"):
            node, metadata = yield self.alice_magicfolder.downloader._get_collective_latest_file(u'blam')
            self.assertTrue(metadata['deleted'])
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)

        with start_action(action_type=u"alice:check"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

    @inline_callbacks
    def test_alice_create_bob_update(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # alice creates a file, bob downloads it
        yield self.alice_fileops.write(alice_fname, 'contents0\n')

        yield iterate(self.alice_magicfolder)
        yield iterate(self.alice_magicfolder)
        yield iterate(self.bob_magicfolder)

        # check the state (XXX ditto, had to switch to veresion 0; right?)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
        self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
            0
        )
        self.failUnlessReallyEqual(
            self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
            1
        )

        yield iterate(self.bob_magicfolder)
        # now bob updates it (bob should upload, alice download)
        yield self.bob_fileops.write(bob_fname, 'bob wuz here\n')

        yield iterate(self.bob_magicfolder)
        yield iterate(self.bob_magicfolder)
        yield iterate(self.alice_magicfolder)

        # check the state
        yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
        self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
        self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

    @inline_callbacks
    def test_download_retry(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        # bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # Alice creates a file
        yield self.alice_fileops.write(alice_fname, ''.join(['contents-%04d\n' % i for i in range(1024)]))
        yield iterate(self.alice_magicfolder)
        # check alice created the file
        yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
        self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)

        # now, we ONLY want to do the scan, not a full iteration of
        # the process loop. So we do just the scan part "by hand" in
        # Bob's downloader
        with start_action(action_type=u"test:perform-scan"):
            yield self.bob_magicfolder.downloader._perform_scan()
            # while we're delving into internals, I guess we might as well
            # confirm that we did queue up an item to download
            self.assertEqual(1, len(self.bob_magicfolder.downloader._deque))

        # break all the servers so the download fails.  count=1 because we
        # only want the download attempted by _process_deque to fail.  After
        # that, we want it to work again.
        for server_id in self.g.get_all_serverids():
            self.g.break_server(server_id, count=1)

        # now let bob try to do the download.  Reach in and call
        # _process_deque directly because we are already half-way through a
        # logical iteration thanks to the _perform_scan call above.
        with start_action(action_type=u"test:process-deque"):
            yield self.bob_magicfolder.downloader._process_deque()

            self.eliot_logger.flushTracebacks(UnrecoverableFileError)
            logged = self.eliot_logger.flushTracebacks(NoSharesError)
            self.assertEqual(
                1,
                len(logged),
                "Got other than expected single NoSharesError: {}".format(logged),
            )

            # ...however Bob shouldn't have downloaded anything
            self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
            # bob should *not* have downloaded anything, as we failed all the servers
            self.failUnlessReallyEqual(
                self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
                0
            )
            self.failUnlessReallyEqual(
                self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
                1
            )

        with start_action(action_type=u"test:iterate"):
            # now we let Bob try again
            yield iterate(self.bob_magicfolder)

            # ...and he should have succeeded
            self.failUnlessReallyEqual(
                self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
                1
            )
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)

    @inline_callbacks
    def test_conflict_local_change_fresh(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'localchange0')
        bob_fname = os.path.join(self.bob_magic_dir, 'localchange0')

        # alice creates a file, bob downloads it
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        yield self.alice_fileops.write(alice_fname, 'contents0\n')
        yield iterate(self.alice_magicfolder)  # for windows

        # before bob downloads, we make a local file for bob by the
        # same name
        with open(bob_fname, 'w') as f:
            f.write("not the right stuff")

        yield iterate_uploader(self.alice_magicfolder)
        yield alice_proc  # alice uploads

        yield iterate_downloader(self.bob_magicfolder)
        yield bob_proc    # bob downloads

        # ...so now bob should produce a conflict
        self.assertTrue(os.path.exists(bob_fname + '.conflict'))

    @inline_callbacks
    def test_conflict_local_change_existing(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'localchange1')
        bob_fname = os.path.join(self.bob_magic_dir, 'localchange1')

        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"alice:create"):
            yield self.alice_fileops.write(alice_fname, 'contents0\n')
            yield iterate(self.alice_magicfolder)  # for windows

        with start_action(action_type=u"alice:upload"):
            yield iterate_uploader(self.alice_magicfolder)
            yield alice_proc  # alice uploads
            self.assertEqual(
                1,
                self._get_count(
                    'uploader.files_uploaded',
                    client=self.alice_magicfolder._client,
                ),
            )

        with start_action(action_type=u"bob:download"):
            yield iterate_downloader(self.bob_magicfolder)
            yield bob_proc    # bob downloads
            self.assertEqual(
                1,
                self._get_count(
                    'downloader.objects_downloaded',
                    client=self.bob_magicfolder._client,
                ),
            )

        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"alice:rewrite"):
            yield self.alice_fileops.write(alice_fname, 'contents1\n')
            yield iterate(self.alice_magicfolder)  # for windows

        with start_action(action_type=u"bob:rewrite"):
            # before bob downloads, make a local change
            with open(bob_fname, "w") as f:
                f.write("bob's local change")

        with start_action(action_type=u"alice:reupload"):
            yield iterate_uploader(self.alice_magicfolder)
            yield alice_proc  # alice uploads
            self.assertEqual(
                2,
                self._get_count(
                    'uploader.files_uploaded',
                    client=self.alice_magicfolder._client,
                ),
            )

        with start_action(action_type=u"bob:redownload-and-conflict"):
            yield iterate_downloader(self.bob_magicfolder)
            yield bob_proc    # bob downloads

            self.assertEqual(
                2,
                self._get_count(
                    'downloader.objects_downloaded',
                    client=self.bob_magicfolder._client,
                ),
            )
            self.assertEqual(
                1,
                self._get_count(
                    'downloader.objects_conflicted',
                    client=self.bob_magicfolder._client,
                ),
            )

            # ...so now bob should produce a conflict
            self.assertTrue(os.path.exists(bob_fname + '.conflict'))

    @inline_callbacks
    def test_alice_delete_and_restore(self):
        alice_fname = os.path.join(self.alice_magic_dir, 'blam')
        bob_fname = os.path.join(self.bob_magic_dir, 'blam')

        # alice creates a file, bob downloads it
        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"alice:create"):
            yield self.alice_fileops.write(alice_fname, 'contents0\n')
            yield iterate(self.alice_magicfolder)  # for windows

        with start_action(action_type=u"alice:upload"):
            yield iterate_uploader(self.alice_magicfolder)
            yield alice_proc  # alice uploads

        with start_action(action_type=u"bob:download"):
            yield iterate_downloader(self.bob_magicfolder)
            yield bob_proc    # bob downloads

        with start_action(action_type=u"alice:check"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 0)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 0)

        with start_action(action_type=u"bob:check"):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 0)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 0)
            yield self.failUnlessReallyEqual(
                self._get_count('downloader.objects_failed', client=self.bob_magicfolder._client),
                0
            )
            yield self.failUnlessReallyEqual(
                self._get_count('downloader.objects_downloaded', client=self.bob_magicfolder._client),
                1
            )
            self.failUnless(os.path.exists(bob_fname))
            self.failUnless(not os.path.exists(bob_fname + '.backup'))
            self.failUnless(not os.path.exists(bob_fname + '.conflict'))

        alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
        bob_proc = self.bob_magicfolder.downloader.set_hook('processed')

        with start_action(action_type=u"alice:delete"):
            yield self.alice_fileops.delete(alice_fname)
            yield iterate_uploader(self.alice_magicfolder)
            yield alice_proc

        with start_action(action_type=u"bob:redownload"):
            yield iterate_downloader(self.bob_magicfolder)
            yield bob_proc

        with start_action(action_type=u"bob:recheck"):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 1)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 1)
            self.assertFalse(os.path.exists(bob_fname))
            self.assertTrue(os.path.exists(bob_fname + '.backup'))
            self.assertFalse(os.path.exists(bob_fname + '.conflict'))

        with start_action(action_type=u"alice:recheck"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 1)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 1)

        with start_action(action_type=u"alice:restore"):
            os.unlink(bob_fname + '.backup')
            alice_proc = self.alice_magicfolder.uploader.set_hook('processed')
            bob_proc = self.bob_magicfolder.downloader.set_hook('processed')
            yield self.alice_fileops.write(alice_fname, 'alice wuz here\n')
            yield iterate(self.alice_magicfolder)  # for windows

        with start_action(action_type=u"alice:reupload"):
            yield iterate_uploader(self.alice_magicfolder)
            yield iterate_downloader(self.alice_magicfolder)  #  why?
            yield alice_proc

        with start_action(action_type=u"bob:final-redownload"):
            yield iterate_downloader(self.bob_magicfolder)
            yield iterate_uploader(self.bob_magicfolder)
            yield bob_proc

        with start_action(action_type=u"bob:final-check"):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"blam", 2)
            yield self._check_version_in_local_db(self.bob_magicfolder, u"blam", 2)
            self.failUnless(os.path.exists(bob_fname))

        with start_action(action_type=u"alice:final-check"):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"blam", 2)
            yield self._check_version_in_local_db(self.alice_magicfolder, u"blam", 2)

    # XXX this should be shortened -- as in, any cases not covered by
    # the other tests in here should get their own minimal test-case.
    @skipIf(sys.platform == "win32", "Still inotify problems on Windows (FIXME)")
    def test_alice_bob(self):
        d = DeferredContext(defer.succeed(None))

        # XXX FIXME just quickly porting this test via aliases -- the
        # "real" solution is to break out any relevant test-cases as
        # their own (smaller!) tests.
        alice_clock = self.alice_magicfolder.uploader._clock
        bob_clock = self.bob_magicfolder.uploader._clock

        def _wait_for_Alice(ign, downloaded_d):
            if _debug: print("Now waiting for Alice to download\n")
            alice_clock.advance(4)
            return downloaded_d

        def _wait_for_Bob(ign, downloaded_d):
            if _debug: print("Now waiting for Bob to download\n")
            bob_clock.advance(4)
            return downloaded_d

        def _wait_for(ign, something_to_do, alice=True):
            if alice:
                downloaded_d = self.bob_magicfolder.downloader.set_hook('processed')
                uploaded_d = self.alice_magicfolder.uploader.set_hook('processed')
            else:
                downloaded_d = self.alice_magicfolder.downloader.set_hook('processed')
                uploaded_d = self.bob_magicfolder.uploader.set_hook('processed')

            d = something_to_do()

            def advance(ign):
                if alice:
                    if _debug: print("Waiting for Alice to upload 3\n")
                    alice_clock.advance(4)
                    uploaded_d.addCallback(_wait_for_Bob, downloaded_d)
                else:
                    if _debug: print("Waiting for Bob to upload\n")
                    bob_clock.advance(4)
                    uploaded_d.addCallback(_wait_for_Alice, downloaded_d)
                return uploaded_d
            d.addCallback(advance)
            return d

        @inline_callbacks
        def Alice_to_write_a_file():
            if _debug: print("Alice writes a file\n\n\n\n\n")
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.alice_magicfolder.uploader._local_path_u)
            yield self.alice_fileops.write(self.file_path, "meow, meow meow. meow? meow meow! meow.")
            yield iterate(self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_write_a_file)

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"file1", 0)
            self._check_version_in_local_db(self.alice_magicfolder, u"file1", 0)
            self._check_uploader_count('objects_failed', 0)
            self._check_uploader_count('objects_succeeded', 1)
            self._check_uploader_count('files_uploaded', 1)
            self._check_uploader_count('objects_queued', 0)
            self._check_uploader_count('directories_created', 0)
            self._check_uploader_count('objects_conflicted', 0)
            self._check_uploader_count('objects_conflicted', 0, magic=self.bob_magicfolder)

            self._check_version_in_local_db(self.bob_magicfolder, u"file1", 0)
            self._check_downloader_count('objects_failed', 0)
            self._check_downloader_count('objects_downloaded', 1)
            self._check_uploader_count('objects_succeeded', 0, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_downloaded', 1, magic=self.bob_magicfolder)
        d.addCallback(check_state)

        @inline_callbacks
        def Alice_to_delete_file():
            if _debug: print("Alice deletes the file!\n\n\n\n")
            yield self.alice_fileops.delete(self.file_path)
            yield iterate(self.alice_magicfolder)
            yield iterate(self.bob_magicfolder)
        d.addCallback(_wait_for, Alice_to_delete_file)

        @inline_callbacks
        def notify_bob_moved(ign):
            # WARNING: this is just directly notifying for the mock
            # tests, because in the Real* tests the .backup file will
            # me moved into place (from the original)
            p = abspath_expanduser_unicode(u"file1", base=self.bob_magicfolder.uploader._local_path_u)
            if self.bob_fileops._fake_inotify:
                self.bob_magicfolder.uploader._notifier.event(to_filepath(p + u'.backup'), fake_inotify.IN_MOVED_TO)
            yield iterate(self.bob_magicfolder)
        d.addCallback(notify_bob_moved)

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"file1", 1)
            self._check_version_in_local_db(self.alice_magicfolder, u"file1", 1)
            self._check_uploader_count('objects_failed', 0)
            self._check_uploader_count('objects_succeeded', 2)
            self._check_uploader_count('objects_succeeded', 0, magic=self.bob_magicfolder)

            self._check_version_in_local_db(self.bob_magicfolder, u"file1", 1)
            self._check_version_in_dmd(self.bob_magicfolder, u"file1", 1)
            self._check_file_gone(self.bob_magicfolder, u"file1")
            self._check_downloader_count('objects_failed', 0)
            self._check_downloader_count('objects_downloaded', 2)
            self._check_downloader_count('objects_downloaded', 2, magic=self.bob_magicfolder)
        d.addCallback(check_state)

        @inline_callbacks
        def Alice_to_rewrite_file():
            if _debug: print("Alice rewrites file\n")
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.alice_magicfolder.uploader._local_path_u)
            yield self.alice_fileops.write(
                self.file_path,
                "Alice suddenly sees the white rabbit running into the forest.",
            )
            yield iterate(self.alice_magicfolder)
        d.addCallback(_wait_for, Alice_to_rewrite_file)
        d.addCallback(lambda ign: iterate(self.bob_magicfolder))

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"file1", 2)
            self._check_version_in_local_db(self.alice_magicfolder, u"file1", 2)
            self._check_uploader_count('objects_failed', 0)
            self._check_uploader_count('objects_succeeded', 3)
            self._check_uploader_count('files_uploaded', 3)
            self._check_uploader_count('objects_queued', 0)
            self._check_uploader_count('directories_created', 0)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 0)

            self._check_version_in_dmd(self.bob_magicfolder, u"file1", 2)
            self._check_version_in_local_db(self.bob_magicfolder, u"file1", 2)
            self._check_downloader_count('objects_failed', 0)
            self._check_downloader_count('objects_downloaded', 3)
            self._check_uploader_count('objects_succeeded', 0, magic=self.bob_magicfolder)
        d.addCallback(check_state)

        path_u = u"/tmp/magic_folder_test"
        encoded_path_u = magicpath.path2magic(u"/tmp/magic_folder_test")

        def Alice_tries_to_p0wn_Bob(ign):
            if _debug: print("Alice tries to p0wn Bob\n")
            iter_d = iterate(self.bob_magicfolder)
            processed_d = self.bob_magicfolder.downloader.set_hook('processed')

            # upload a file that would provoke the security bug from #2506
            uploadable = Data("", self.alice_magicfolder._client.convergence)
            alice_dmd = self.alice_magicfolder.uploader._upload_dirnode

            d2 = alice_dmd.add_file(encoded_path_u, uploadable, metadata={"version": 0}, overwrite=True)
            d2.addCallback(lambda ign: self.failUnless(alice_dmd.has_child(encoded_path_u)))
            d2.addCallback(lambda ign: iter_d)
            d2.addCallback(_wait_for_Bob, processed_d)
            return d2
        d.addCallback(Alice_tries_to_p0wn_Bob)

        @log_call(action_type=u"check_state", include_args=[], include_result=False)
        def check_state(ignored):
            self.failIf(os.path.exists(path_u))
            self._check_version_in_local_db(self.bob_magicfolder, encoded_path_u, None)
            self._check_downloader_count('objects_downloaded', 3)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 0)
        d.addCallback(check_state)

        @inline_callbacks
        def Bob_to_rewrite_file():
            if _debug: print("Bob rewrites file\n")
            self.file_path = abspath_expanduser_unicode(u"file1", base=self.bob_magicfolder.uploader._local_path_u)
            if _debug: print("---- bob's file is %r" % (self.file_path,))
            yield self.bob_fileops.write(self.file_path, "No white rabbit to be found.")
            yield iterate(self.bob_magicfolder)
        d.addCallback(lambda ign: _wait_for(None, Bob_to_rewrite_file, alice=False))

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"file1", 3)
            self._check_version_in_local_db(self.bob_magicfolder, u"file1", 3)
            self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_succeeded', 1, magic=self.bob_magicfolder)
            self._check_uploader_count('files_uploaded', 1, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.bob_magicfolder)

            self._check_version_in_dmd(self.alice_magicfolder, u"file1", 3)
            self._check_version_in_local_db(self.alice_magicfolder, u"file1", 3)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_downloaded', 1, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
        d.addCallback(check_state)

        def Alice_conflicts_with_Bobs_last_downloaded_uri():
            if _debug: print("Alice conflicts with Bob\n")
            downloaded_d = self.bob_magicfolder.downloader.set_hook('processed')
            uploadable = Data("do not follow the white rabbit", self.alice_magicfolder._client.convergence)
            alice_dmd = self.alice_magicfolder.uploader._upload_dirnode
            d2 = alice_dmd.add_file(u"file1", uploadable,
                                    metadata={"version": 5,
                                              "last_downloaded_uri" : "URI:LIT:" },
                                    overwrite=True)
            if _debug: print("Waiting for Alice to upload\n")
            d2.addCallback(lambda ign: bob_clock.advance(6))
            d2.addCallback(lambda ign: downloaded_d)
            d2.addCallback(lambda ign: self.failUnless(alice_dmd.has_child(encoded_path_u)))
            return d2
        d.addCallback(lambda ign: Alice_conflicts_with_Bobs_last_downloaded_uri())

        @log_call(action_type=u"check_state", include_args=[], include_result=False)
        def check_state(ignored):
            self._check_downloader_count('objects_downloaded', 4, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_conflicted', 1, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_downloaded', 1, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_uploader_count('files_uploaded', 1, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_succeeded', 1, magic=self.bob_magicfolder)
        d.addCallback(check_state)

        # prepare to perform another conflict test
        @log_call_deferred(action_type=u"alice:to-write:file2")
        @inline_callbacks
        def Alice_to_write_file2():
            if _debug: print("Alice writes a file2\n")
            self.file_path = abspath_expanduser_unicode(u"file2", base=self.alice_magicfolder.uploader._local_path_u)
            d = self.alice_fileops.write(self.file_path, "something")
            self.bob_clock.advance(4)
            yield d
        d.addCallback(_wait_for, Alice_to_write_file2)

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"file2", 0)
            self._check_version_in_local_db(self.alice_magicfolder, u"file2", 0)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_uploader_count('files_uploaded', 1, magic=self.bob_magicfolder)
        d.addCallback(check_state)

        def advance(ign):
            alice_clock.advance(4)
            bob_clock.advance(4)
            # we need to pause here, or make "is_new_file()" more
            # robust, because this is now fast enough that the mtime
            # of the allegedly-new file matches, so Bob decides not to
            # upload (and the test hangs). Not sure why it worked
            # before; must have been *just* slow enough?
            # XXX FIXME for the new real-test had to jack this to 0.5;
            # related to the 0.1 notify pause??
            return task.deferLater(reactor, 0.5, lambda: None)
        d.addCallback(advance)
        d.addCallback(lambda ign: self._check_version_in_local_db(self.bob_magicfolder, u"file2", 0))

        @inline_callbacks
        def Bob_to_rewrite_file2():
            if _debug: print("Bob rewrites file2\n")
            self.file_path = abspath_expanduser_unicode(u"file2", base=self.bob_magicfolder.uploader._local_path_u)
            if _debug: print("---- bob's file is %r" % (self.file_path,))
            yield iterate(self.bob_magicfolder)
            yield self.bob_fileops.write(self.file_path, "roger roger. what vector?")
            if _debug: print("---- bob rewrote file2")
            yield iterate(self.bob_magicfolder)
            if _debug: print("---- iterated bob's magicfolder")
        d.addCallback(lambda ign: _wait_for(None, Bob_to_rewrite_file2, alice=False))

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"file2", 1)
            self._check_downloader_count('objects_downloaded', 5, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_conflicted', 1, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_succeeded', 2, magic=self.bob_magicfolder)
            self._check_uploader_count('files_uploaded', 2, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder)
        d.addCallback(check_state)

        # XXX here we advance the clock and then test again to make sure no values are monotonically increasing
        # with each queue turn ;-p
        alice_clock.advance(6)
        bob_clock.advance(6)

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"file2", 1)
            self._check_downloader_count('objects_downloaded', 5)
            self._check_downloader_count('objects_conflicted', 1)
            self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_succeeded', 2, magic=self.bob_magicfolder)
            self._check_uploader_count('files_uploaded', 2, magic=self.bob_magicfolder)
            self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder)
            self._check_uploader_count('files_uploaded', 2, magic=self.bob_magicfolder)
        d.addCallback(check_state)

        def Alice_conflicts_with_Bobs_last_uploaded_uri():
            if _debug: print("Alice conflicts with Bob\n")
            encoded_path_u = magicpath.path2magic(u"file2")
            downloaded_d = self.bob_magicfolder.downloader.set_hook('processed')
            uploadable = Data("rabbits with sharp fangs", self.alice_magicfolder._client.convergence)
            alice_dmd = self.alice_magicfolder.uploader._upload_dirnode
            d2 = alice_dmd.add_file(u"file2", uploadable,
                                    metadata={"version": 5,
                                              "last_uploaded_uri" : "URI:LIT:" },
                                    overwrite=True)
            if _debug: print("Waiting for Alice to upload\n")
            d2.addCallback(lambda ign: bob_clock.advance(6))
            d2.addCallback(lambda ign: downloaded_d)
            d2.addCallback(lambda ign: self.failUnless(alice_dmd.has_child(encoded_path_u)))
            return d2
        d.addCallback(lambda ign: Alice_conflicts_with_Bobs_last_uploaded_uri())

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"file2", 5)
            self._check_downloader_count('objects_downloaded', 6)
            self._check_downloader_count('objects_conflicted', 1)
            self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_succeeded', 2, magic=self.bob_magicfolder)
            self._check_uploader_count('files_uploaded', 2, magic=self.bob_magicfolder)
            self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder)
        d.addCallback(check_state)

        def foo(ign):
            alice_clock.advance(6)
            bob_clock.advance(6)
            alice_clock.advance(6)
            bob_clock.advance(6)
        d.addCallback(foo)

        @log_call(action_type=u"check_state", include_args=[], include_result=False)
        def check_state(ignored):
            self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 1)
            self._check_downloader_count('objects_downloaded', 6)
        d.addCallback(check_state)

        # prepare to perform another conflict test
        @inline_callbacks
        def Alice_to_write_file3():
            if _debug: print("Alice writes a file\n")
            self.file_path = abspath_expanduser_unicode(u"file3", base=self.alice_magicfolder.uploader._local_path_u)
            yield self.alice_fileops.write(self.file_path, "something")
            yield iterate(self.alice_magicfolder)
            # Make sure Bob gets the file before we do anything else.
            yield iterate(self.bob_magicfolder)
        d.addCallback(_wait_for, Alice_to_write_file3)

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.alice_magicfolder, u"file3", 0)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_downloaded', 7)
            self._check_downloader_count('objects_downloaded', 2, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_conflicted', 1)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
        d.addCallback(check_state)

        @inline_callbacks
        def Bob_to_rewrite_file3():
            if _debug: print("Bob rewrites file3\n")
            self.file_path = abspath_expanduser_unicode(u"file3", base=self.bob_magicfolder.uploader._local_path_u)
            if _debug: print("---- bob's file is %r" % (self.file_path,))
            yield iterate(self.bob_magicfolder)
            yield self.bob_fileops.write(self.file_path, "roger roger")
            yield iterate(self.bob_magicfolder)
        d.addCallback(lambda ign: _wait_for(None, Bob_to_rewrite_file3, alice=False))

        @log_call_deferred(action_type=u"check_state")
        @inline_callbacks
        def check_state(ignored):
            yield self._check_version_in_dmd(self.bob_magicfolder, u"file3", 1)
            self._check_downloader_count('objects_downloaded', 7)
            self._check_downloader_count('objects_conflicted', 1)
            self._check_uploader_count('objects_failed', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_succeeded', 3, magic=self.bob_magicfolder)
            self._check_uploader_count('files_uploaded', 3, magic=self.bob_magicfolder)
            self._check_uploader_count('objects_queued', 0, magic=self.bob_magicfolder)
            self._check_uploader_count('directories_created', 0, magic=self.bob_magicfolder)
            self._check_downloader_count('objects_conflicted', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_failed', 0, magic=self.alice_magicfolder)
            self._check_downloader_count('objects_downloaded', 3, magic=self.alice_magicfolder)
        d.addCallback(check_state)

        return d.addActionFinish()


class SingleMagicFolderTestMixin(MagicFolderCLITestMixin, ShouldFailMixin, ReallyEqualMixin, CheckerMixin):
    """
    These tests will be run both with a mock notifier, and (on platforms that support it)
    with the real INotify.
    """

    def setUp(self):
        self.assertIs(None, super(SingleMagicFolderTestMixin, self).setUp())
        temp = self.mktemp()
        self.basedir = abspath_expanduser_unicode(temp.decode(get_filesystem_encoding()))
        self.magicfolder = None
        self.set_up_grid(oneshare=True)
        self.local_dir = os.path.join(self.basedir, u"local_dir")
        self.mkdir_nonascii(self.local_dir)

        # Magic-folder implementation somehow manages to leave a DelayedCall
        # in the reactor from the eventual queue by the end of the test.  It
        # may have something to do with the upload process but it's not
        # entirely clear.  It's difficult to track things through the eventual
        # queue.  It is almost certainly the case that some other Deferred
        # involved in magic-folder that is already being waited on elsewhere
        # *should* encompass this DelayedCall but I wasn't able to figure out
        # where that association needs to be made.  So, as a work-around,
        # explicitly flush the eventual queue at the end of the test, too.
        from foolscap.eventual import flushEventualQueue
        self.addCleanup(flushEventualQueue)

        # Sometimes a collective scan fails with UnrecoverableFileError.  It's
        # not clear to me why. :/ This fixes the issue, though, and all other
        # asserted-about behavior is provided whether this case is hit or not.
        self.addCleanup(
            lambda: self.eliot_logger.flushTracebacks(UnrecoverableFileError)
        )

        d = DeferredContext(self.create_invite_join_magic_folder(self.alice_nickname, self.local_dir))
        d.addCallback(self._restart_client)
        # note: _restart_client ultimately sets self.magicfolder to not-None
        return d.result

    def tearDown(self):
        d = DeferredContext(super(SingleMagicFolderTestMixin, self).tearDown())
        d.addCallback(self.cleanup)
        return d.result

    def _createdb(self):
        dbfile = abspath_expanduser_unicode(u"magicfolder_default.sqlite", base=self.basedir)
        mdb = magicfolderdb.get_magicfolderdb(dbfile, create_version=(magicfolderdb.SCHEMA_v1, 1))
        self.failUnless(mdb, "unable to create magicfolderdb from %r" % (dbfile,))
        self.failUnlessEqual(mdb.VERSION, 1)
        return mdb

    @log_call_deferred(action_type=u"restart-client")
    def _restart_client(self, ign):
        #print "_restart_client"
        d = DeferredContext(self.restart_client())
        d.addCallback(self._wait_until_started)
        return d.result

    @log_call_deferred(action_type=u"wait-until-started")
    def _wait_until_started(self, ign):
        #print "_wait_until_started"
        self.magicfolder = self.get_client().getServiceNamed('magic-folder-default')
        self.fileops = FileOperationsHelper(self.magicfolder.uploader, self.inject_inotify)
        self.up_clock = task.Clock()
        self.down_clock = task.Clock()
        self.magicfolder.uploader._clock = self.up_clock
        self.magicfolder.downloader._clock = self.down_clock

        # XXX should probably be passing the reactor to instances when
        # they're created, but that's a ton of re-factoring, so we
        # side-step that issue by hacking it in here. However, we
        # *have* to "hack it in" before we call ready() so that the
        # first iteration of the loop doesn't call the "real"
        # reactor's callLater. :(
        return self.magicfolder.ready()

    def test_db_basic(self):
        fileutil.make_dirs(self.basedir)
        self._createdb()

    @inline_callbacks
    def test_scan_once_on_startup(self):
        # What is this test? Maybe it is just a stub and needs finishing.
        self.magicfolder.uploader._clock.advance(99)

        yield self._check_uploader_count('files_uploaded', 0, magic=self.magicfolder)
        yield self._check_uploader_count('objects_queued', 0, magic=self.magicfolder)
        yield self._check_downloader_count('objects_conflicted', 0, magic=self.magicfolder)
        yield self._check_uploader_count('objects_succeeded', 0, magic=self.magicfolder)
        yield self._check_downloader_count('objects_failed', 0, magic=self.magicfolder)
        yield self._check_downloader_count('objects_downloaded', 0, magic=self.magicfolder)

    def test_db_persistence(self):
        """Test that a file upload creates an entry in the database."""

        fileutil.make_dirs(self.basedir)
        db = self._createdb()

        relpath1 = u"myFile1"
        pathinfo = fileutil.PathInfo(isdir=False, isfile=True, islink=False,
                                     exists=True, size=1, mtime_ns=123, ctime_ns=456)
        db.did_upload_version(relpath1, 0, 'URI:LIT:1', 'URI:LIT:0', 0, pathinfo)

        c = db.cursor
        c.execute("SELECT size, mtime_ns, ctime_ns"
                  " FROM local_files"
                  " WHERE path=?",
                  (relpath1,))
        row = c.fetchone()
        self.failUnlessEqual(row, (pathinfo.size, pathinfo.mtime_ns, pathinfo.ctime_ns))

        # Second test uses magic_folder.is_new_file instead of SQL query directly
        # to confirm the previous upload entry in the db.
        relpath2 = u"myFile2"
        path2 = os.path.join(self.basedir, relpath2)
        fileutil.write(path2, "meow\n")
        pathinfo = fileutil.get_pathinfo(path2)
        db.did_upload_version(relpath2, 0, 'URI:LIT:2', 'URI:LIT:1', 0, pathinfo)
        db_entry = db.get_db_entry(relpath2)
        self.assertFalse(magic_folder.is_new_file(pathinfo, db_entry))

        different_pathinfo = fileutil.PathInfo(isdir=False, isfile=True, islink=False,
                                               exists=True, size=0, mtime_ns=pathinfo.mtime_ns,
                                               ctime_ns=pathinfo.ctime_ns)
        self.assertTrue(magic_folder.is_new_file(different_pathinfo, db_entry))

    def _test_magicfolder_start_service(self):
        # what is this even testing?
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 0))

        d.addCallback(lambda ign: self.create_invite_join_magic_folder(self.alice_nickname, self.local_dir))
        d.addCallback(self._restart_client)

        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 1))
        d.addBoth(self.cleanup)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.dirs_monitored'), 0))
        return d

    @skipIf(sys.platform == "linux2", "fails on certain linux flavors: see ticket #2834")
    def test_move_tree(self):
        """
        create an empty directory tree and 'mv' it into the magic folder,
        noting the new directory and uploading it.

        also creates a directory tree with one file in it and 'mv's it
        into the magic folder, so we upload the file and record the
        directory. (XXX split to separate test)
        """
        empty_tree_name = self.unicode_or_fallback(u"empty_tr\u00EAe", u"empty_tree")
        empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.basedir)
        new_empty_tree_dir = abspath_expanduser_unicode(empty_tree_name, base=self.local_dir)

        small_tree_name = self.unicode_or_fallback(u"small_tr\u00EAe", u"empty_tree")
        small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.basedir)
        new_small_tree_dir = abspath_expanduser_unicode(small_tree_name, base=self.local_dir)

        d = DeferredContext(defer.succeed(None))

        @inline_callbacks
        def _check_move_empty_tree(res):
            self.mkdir_nonascii(empty_tree_dir)
            yield self.fileops.move(empty_tree_dir, new_empty_tree_dir)
            yield iterate(self.magicfolder)

        d.addCallback(_check_move_empty_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 1))

        @inline_callbacks
        def _check_move_small_tree(res):
            self.mkdir_nonascii(small_tree_dir)
            what_path = abspath_expanduser_unicode(u"what", base=small_tree_dir)
            fileutil.write(what_path, "say when")
            yield self.fileops.move(small_tree_dir, new_small_tree_dir)
            upstatus = list(self.magicfolder.uploader.get_status())
            downstatus = list(self.magicfolder.downloader.get_status())

            self.assertEqual(2, len(upstatus))
            self.assertEqual(0, len(downstatus))
            yield iterate(self.magicfolder)

            # when we add the dir, we queue a scan of it; so we want
            # the upload to "go" as well requiring 1 more iteration
            yield iterate(self.magicfolder)

        d.addCallback(_check_move_small_tree)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 3))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        @inline_callbacks
        def _check_moved_tree_is_watched(res):
            another_path = abspath_expanduser_unicode(u"another", base=new_small_tree_dir)
            yield self.fileops.write(another_path, "file")
            yield iterate(self.magicfolder)
            yield iterate(self.magicfolder)  # windows; why?

        d.addCallback(_check_moved_tree_is_watched)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 4))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 2))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.directories_created'), 2))

        return d.result

    def test_persistence(self):
        """
        Perform an upload of a given file and then stop the client.
        Start a new client and magic-folder service... and verify that the file is NOT uploaded
        a second time. This test is meant to test the database persistence along with
        the startup and shutdown code paths of the magic-folder service.
        """
        self.collective_dircap = "" # XXX hmmm?

        d = DeferredContext(defer.succeed(None))

        @inline_callbacks
        def create_test_file(filename):
            test_file = abspath_expanduser_unicode(filename, base=self.local_dir)
            yield self.fileops.write(test_file, "meow %s" % filename)
            yield iterate(self.magicfolder)
            yield iterate(self.magicfolder)  # windows; why?

        d.addCallback(lambda ign: create_test_file(u"what1"))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(self.cleanup)

        d.addCallback(self._restart_client)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        d.addCallback(lambda ign: create_test_file(u"what2"))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_succeeded'), 1))
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_queued'), 0))
        return d.result

    # all this "self.*" state via 9000 mix-ins is really really
    # hard to read, keep track of, etc. Very hard to understand
    # what each test uses for setup, etc. :(

    @inline_callbacks
    def test_delete(self):
        # setup: create a file 'foo'
        path = os.path.join(self.local_dir, u'foo')
        yield self.fileops.write(path, 'foo\n')
        yield iterate_uploader(self.magicfolder)
        yield iterate_uploader(self.magicfolder)  # req'd for windows; not sure why?
        self.assertTrue(os.path.exists(path))
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))

        # the test: delete the file (and do fake notifies)
        yield self.fileops.delete(path)

        yield iterate_uploader(self.magicfolder)
        self.assertFalse(os.path.exists(path))

        yield iterate_downloader(self.magicfolder)
        # ensure we still have a DB entry, and that the version is 1
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))
        self.failUnlessEqual(metadata['version'], 1)

    @inline_callbacks
    def test_batched_process(self):
        """
        status APIs correctly function when there are 2 items queued at
        once for processing
        """
        # setup: get at least two items into the deque
        path0 = os.path.join(self.local_dir, u'foo')
        yield self.fileops.write(path0, 'foo\n')
        path1 = os.path.join(self.local_dir, u'bar')
        yield self.fileops.write(path1, 'bar\n')

        # get the status before we've processed anything
        upstatus0 = list(self.magicfolder.uploader.get_status())
        upstatus1 = []

        def one_item(item):
            # grab status after we've processed a single item
            us = list(self.magicfolder.uploader.get_status())
            upstatus1.extend(us)
        one_d = self.magicfolder.uploader.set_hook('item_processed')
        # can't 'yield' here because the hook isn't called until
        # inside iterate()
        one_d.addCallbacks(one_item, self.fail)

        yield iterate_uploader(self.magicfolder)
        yield iterate_uploader(self.magicfolder)  # req'd for windows; not sure why?

        # no matter which part of the queue the items are in, we
        # should see the same status from the outside
        self.assertEqual(upstatus0, upstatus1)

    @inline_callbacks
    def test_real_notify_failure(self):
        """
        Simulate an exception from the _real_notify helper in
        magic-folder's uploader, confirming error-handling works.
        """

        orig_notify = self.magicfolder.uploader._real_notify

        class BadStuff(Exception):
            pass

        def bad_stuff(*args, **kw):
            # call original method ..
            orig_notify(*args, **kw)
            # ..but then cause a special problem
            raise BadStuff("the bad stuff")

        patch_notify = mock.patch.object(
            self.magicfolder.uploader,
            '_real_notify',
            mock.Mock(side_effect=bad_stuff),
        )
        with patch_notify:
            path0 = os.path.join(self.local_dir, u'foo')
            yield self.fileops.write(path0, 'foo\n')
            # this actually triggers two notifies

        # do a reactor turn; this is necessary because our "bad_stuff"
        # method calls the hook (so the above 'yield' resumes) right
        # *before* it raises the exception; thus, we ensure all the
        # pending callbacks including the exception are processed
        # before we flush the errors.
        yield task.deferLater(reactor, 0, lambda: None)

        errors = self.eliot_logger.flushTracebacks(BadStuff)
        # it seems on Windows the "RealTest" variant only produces 1
        # notification for some reason..
        self.assertTrue(len(errors) >= 1)

    @inline_callbacks
    def test_delete_and_restore(self):
        # setup: create a file
        path = os.path.join(self.local_dir, u'foo')
        yield self.fileops.write(path, 'foo\n')
        yield iterate_uploader(self.magicfolder)
        yield iterate_uploader(self.magicfolder)  # req'd for windows; why?
        self.assertTrue(os.path.exists(path))

        # ...and delete the file
        yield self.fileops.delete(path)
        yield iterate_uploader(self.magicfolder)
        self.assertFalse(os.path.exists(path))

        # ensure we still have a DB entry, and that the version is 1
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))
        self.failUnlessEqual(metadata['version'], 1)

        # restore the file, with different contents
        path = os.path.join(self.local_dir, u'foo')
        yield self.fileops.write(path, 'bar\n')
        yield iterate_uploader(self.magicfolder)

        # ensure we still have a DB entry, and that the version is 2
        node, metadata = yield self.magicfolder.downloader._get_collective_latest_file(u'foo')
        self.assertTrue(node is not None, "Failed to find %r in DMD" % (path,))
        self.failUnlessEqual(metadata['version'], 2)

    def test_write_short_file(self):
        # Write something short enough for a LIT file.
        return self._check_file(u"short", "test")

    def test_magic_folder(self):
        d = DeferredContext(defer.succeed(None))
        # Write something short enough for a LIT file.
        d.addCallback(lambda ign: self._check_file(u"short", "test"))

        # Write to the same file again with different data.
        d.addCallback(lambda ign: self._check_file(u"short", "different"))

        # Test that temporary files are not uploaded.
        d.addCallback(lambda ign: self._check_file(u"tempfile", "test", temporary=True))

        # Test creation of a subdirectory.
        d.addCallback(lambda ign: self._check_mkdir(u"directory"))

        # Write something longer, and also try to test a Unicode name if the fs can represent it.
        name_u = self.unicode_or_fallback(u"l\u00F8ng", u"long")
        d.addCallback(lambda ign: self._check_file(name_u, "test"*100))

        # TODO: test that causes an upload failure.
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.objects_failed'), 0))

        return d.result

    @inline_callbacks
    def _create_directory_with_file(self, relpath_u, content):
        path_f = os.path.join(self.local_dir, relpath_u)
        path_d = os.path.dirname(path_f)
        # Create a new directory in the monitored directory.
        yield self.fileops.mkdir(path_d)
        # Give the system a chance to notice and process it.
        yield iterate(self.magicfolder)
        # Create a new file in that new directory.
        yield self.fileops.write(path_f, content)
        # Another opportunity to process.
        yield iterate(self.magicfolder)

    @inline_callbacks
    def test_create_file_in_sub_directory(self):
        reldir_u = u'subdir'
        # The OS and the DMD may have conflicting conventions for directory
        # the separator.  Construct a value for each.
        dmd_relpath_u = u'/'.join((reldir_u, u'some-file'))
        platform_relpath_u = join(reldir_u, u'some-file')
        content = u'some great content'
        yield self._create_directory_with_file(
            platform_relpath_u,
            content,
        )
        # The new directory and file should have been noticed and uploaded.
        downloader = self.magicfolder.downloader
        encoded_dir_u = magicpath.path2magic(reldir_u + u"/")
        encoded_path_u = magicpath.path2magic(dmd_relpath_u)

        with start_action(action_type=u"retrieve-metadata"):
            dir_node, dir_meta = yield downloader._get_collective_latest_file(
                encoded_dir_u,
            )
            path_node, path_meta = yield downloader._get_collective_latest_file(
                encoded_path_u,
            )

        self.expectThat(dir_node, Not(Is(None)), "dir node")
        self.expectThat(dir_meta, ContainsDict({'version': Equals(0)}), "dir meta")
        self.expectThat(path_node, Not(Is(None)), "path node")
        self.expectThat(path_meta, ContainsDict({'version': Equals(0)}), "path meta")

    @inline_callbacks
    def test_delete_file_in_sub_directory(self):
        dmd_relpath_u = u'/'.join((u'subdir', u'some-file'))
        platform_relpath_u = join(u'subdir', u'some-file')
        content = u'some great content'
        yield self._create_directory_with_file(
            platform_relpath_u,
            content,
        )
        # Delete the file in the sub-directory.
        yield self.fileops.delete(os.path.join(self.local_dir, platform_relpath_u))
        # Let the deletion be processed.
        yield iterate(self.magicfolder)
        # Verify the deletion was uploaded.
        encoded_path_u = magicpath.path2magic(dmd_relpath_u)
        downloader = self.magicfolder.downloader
        node, metadata = yield downloader._get_collective_latest_file(encoded_path_u)
        self.assertThat(node, Not(Is(None)))
        self.assertThat(metadata['version'], Equals(1))
        self.assertThat(metadata['deleted'], Equals(True))

    def test_delete_sub_directory_containing_file(self):
        reldir_u = u'subdir'
        relpath_u = os.path.join(reldir_u, u'some-file')
        content = u'some great content'
        yield self._create_directory_with_file(
            relpath_u,
            content,
        )
        # Delete the sub-directory and the file in it. Don't wait in between
        # because the case where all events are delivered before any
        # processing happens is interesting.  And don't use the fileops API to
        # delete the contained file so that we don't necessarily generate a
        # notification for that path at all.  We require that the
        # implementation behave correctly when receiving only the notification
        # for the containing directory.
        os.unlink(os.path.join(self.local_dir, relpath_u))
        yield self.fileops.delete(os.path.join(self.local_dir, reldir_u))

        # Now allow processing.
        yield iterate(self.magicfolder)
        # Give it some extra time because of recursive directory processing.
        yield iterate(self.magicfolder)

        # Deletion of both entities should have been uploaded.
        downloader = self.magicfolder.downloader
        encoded_dir_u = magicpath.path2magic(reldir_u + u"/")
        encoded_path_u = magicpath.path2magic(relpath_u)

        dir_node, dir_meta = yield downloader._get_collective_latest_file(encoded_dir_u)
        path_node, path_meta = yield downloader._get_collective_latest_file(encoded_path_u)

        self.expectThat(dir_node, Not(Is(None)), "dir node")
        self.expectThat(dir_meta, ContainsDict({
            "version": Equals(1),
            "deleted": Equals(True),
        }), "dir meta")

        self.expectThat(path_node, Not(Is(None)), "path node")
        self.expectThat(path_meta, ContainsDict({
            "version": Equals(1),
            "deleted": Equals(True),
        }), "path meta")


@skipIf(support_missing, support_message)
class MockTestAliceBob(MagicFolderAliceBobTestMixin, AsyncTestCase):
    inject_inotify = True

    def setUp(self):
        self.inotify = fake_inotify
        self.patch(magic_folder, 'get_inotify_module', lambda: self.inotify)
        return super(MockTestAliceBob, self).setUp()


@skipIf(support_missing, support_message)
class MockTest(SingleMagicFolderTestMixin, AsyncTestCase):
    """This can run on any platform, and even if twisted.internet.inotify can't be imported."""
    inject_inotify = True

    def setUp(self):
        self.inotify = fake_inotify
        self.patch(magic_folder, 'get_inotify_module', lambda: self.inotify)
        return super(MockTest, self).setUp()

    def test_errors(self):
        self.set_up_grid(oneshare=True)

        errors_dir = abspath_expanduser_unicode(u"errors_dir", base=self.basedir)
        os.mkdir(errors_dir)
        not_a_dir = abspath_expanduser_unicode(u"NOT_A_DIR", base=self.basedir)
        fileutil.write(not_a_dir, "")
        magicfolderdb = abspath_expanduser_unicode(u"magicfolderdb", base=self.basedir)
        doesnotexist  = abspath_expanduser_unicode(u"doesnotexist", base=self.basedir)

        client = self.g.clients[0]
        d = DeferredContext(client.create_dirnode())
        def _check_errors(n):
            self.failUnless(IDirectoryNode.providedBy(n))
            upload_dircap = n.get_uri()
            readonly_dircap = n.get_readonly_uri()

            self.shouldFail(ValueError, 'does not exist', 'does not exist',
                            MagicFolder, client, upload_dircap, '', doesnotexist, magicfolderdb, 0o077, 'default')
            self.shouldFail(ValueError, 'is not a directory', 'is not a directory',
                            MagicFolder, client, upload_dircap, '', not_a_dir, magicfolderdb, 0o077, 'default')
            self.shouldFail(AssertionError, 'bad upload.dircap', 'does not refer to a directory',
                            MagicFolder, client, 'bad', '', errors_dir, magicfolderdb, 0o077, 'default')
            self.shouldFail(AssertionError, 'non-directory upload.dircap', 'does not refer to a directory',
                            MagicFolder, client, 'URI:LIT:foo', '', errors_dir, magicfolderdb, 0o077, 'default')
            self.shouldFail(AssertionError, 'readonly upload.dircap', 'is not a writecap to a directory',
                            MagicFolder, client, readonly_dircap, '', errors_dir, magicfolderdb, 0o077, 'default')
            self.shouldFail(AssertionError, 'collective dircap', 'is not a readonly cap to a directory',
                            MagicFolder, client, upload_dircap, upload_dircap, errors_dir, magicfolderdb, 0o077, 'default')

            def _not_implemented():
                raise NotImplementedError("blah")
            self.patch(magic_folder, 'get_inotify_module', _not_implemented)
            self.shouldFail(NotImplementedError, 'unsupported', 'blah',
                            MagicFolder, client, upload_dircap, '', errors_dir, magicfolderdb, 0o077, 'default')
        d.addCallback(_check_errors)
        return d.result

    def test_write_downloaded_file(self):
        workdir = fileutil.abspath_expanduser_unicode(u"cli/MagicFolder/write-downloaded-file")
        local_file = fileutil.abspath_expanduser_unicode(u"foobar", base=workdir)

        class TestWriteFileMixin(WriteFileMixin):
            def _log(self, msg):
                pass

        writefile = TestWriteFileMixin()
        writefile._umask = 0o077

        # create a file with name "foobar" with content "foo"
        # write downloaded file content "bar" into "foobar" with is_conflict = False
        fileutil.make_dirs(workdir)
        fileutil.write(local_file, "foo")

        # if is_conflict is False, then the .conflict file shouldn't exist.
        now = time.time()
        writefile._write_downloaded_file(workdir, local_file, "bar", False, now=now)
        conflicted_path = local_file + u".conflict"
        self.failIf(os.path.exists(conflicted_path))

        # no backup
        backup_path = local_file + u".backup"
        self.failIf(os.path.exists(backup_path))

        # .tmp file shouldn't exist
        self.failIf(os.path.exists(local_file + u".tmp"))

        # The original file should have the new content
        self.failUnlessEqual(fileutil.read(local_file), "bar")

        # .. and approximately the correct timestamp.
        pathinfo = fileutil.get_pathinfo(local_file)
        error_ns = pathinfo.mtime_ns - fileutil.seconds_to_ns(now - WriteFileMixin.FUDGE_SECONDS)
        permitted_error_ns = fileutil.seconds_to_ns(WriteFileMixin.FUDGE_SECONDS)/4
        self.failUnless(abs(error_ns) < permitted_error_ns, (error_ns, permitted_error_ns))

        # now a test for conflicted case
        writefile._write_downloaded_file(workdir, local_file, "bar", True, None)
        self.failUnless(os.path.exists(conflicted_path))

        # .tmp file shouldn't exist
        self.failIf(os.path.exists(local_file + u".tmp"))

    def test_periodic_full_scan(self):
        """
        Create a file in a subdir without doing a notify on it and
        fast-forward time to prove we do a full scan periodically.
        """
        sub_dir = abspath_expanduser_unicode(u"subdir", base=self.local_dir)
        self.mkdir_nonascii(sub_dir)

        d = DeferredContext(defer.succeed(None))

        def _create_file_without_event(res):
            processed_d = self.magicfolder.uploader.set_hook('processed')
            what_path = abspath_expanduser_unicode(u"what", base=sub_dir)
            fileutil.write(what_path, "say when")
            self.magicfolder.uploader._clock.advance(self.magicfolder.uploader._periodic_full_scan_duration + 1)
            # this will have now done the full scan, so we have to do
            # an iteration to process anything from it
            iterate_d = iterate_uploader(self.magicfolder)
            return processed_d.addCallback(lambda ignored: iterate_d)
        d.addCallback(_create_file_without_event)
        def _advance_clock(res):
            processed_d = self.magicfolder.uploader.set_hook('processed')
            self.magicfolder.uploader._clock.advance(4)
            return processed_d
        d.addCallback(_advance_clock)
        d.addCallback(lambda ign: self.failUnlessReallyEqual(self._get_count('uploader.files_uploaded'), 1))
        return d.result

    def test_statistics(self):
        d = DeferredContext(defer.succeed(None))
        # Write something short enough for a LIT file.
        d.addCallback(lambda ign: self._check_file(u"short", "test"))

        # test magic-folder statistics
        d.addCallback(lambda res: self.GET("statistics"))
        def _got_stats(res):
            self.assertIn("Operational Statistics", res)
            self.assertIn("Magic Folder", res)
            self.assertIn("<li>Local Directories Monitored: 1 directories</li>", res)
            self.assertIn("<li>Files Uploaded: 1 files</li>", res)
            self.assertIn("<li>Files Queued for Upload: 0 files</li>", res)
            self.assertIn("<li>Failed Uploads: 0 files</li>", res)
            self.assertIn("<li>Files Downloaded: 0 files</li>", res)
            self.assertIn("<li>Files Queued for Download: 0 files</li>", res)
            self.assertIn("<li>Failed Downloads: 0 files</li>", res)
        d.addCallback(_got_stats)
        d.addCallback(lambda res: self.GET("statistics?t=json"))
        def _got_stats_json(res):
            data = json.loads(res)
            self.assertEqual(data["counters"]["magic_folder.uploader.dirs_monitored"], 1)
            self.assertEqual(data["counters"]["magic_folder.uploader.objects_succeeded"], 1)
            self.assertEqual(data["counters"]["magic_folder.uploader.files_uploaded"], 1)
            self.assertEqual(data["counters"]["magic_folder.uploader.objects_queued"], 0)
        d.addCallback(_got_stats_json)
        return d.result


@skipIf(support_missing, support_message)
class RealTest(SingleMagicFolderTestMixin, AsyncTestCase):
    """This is skipped unless both Twisted and the platform support inotify."""
    inject_inotify = False

    def setUp(self):
        d = super(RealTest, self).setUp()
        self.inotify = magic_folder.get_inotify_module()
        return d


@skipIf(support_missing, support_message)
class RealTestAliceBob(MagicFolderAliceBobTestMixin, AsyncTestCase):
    """This is skipped unless both Twisted and the platform support inotify."""
    inject_inotify = False

    def setUp(self):
        d = super(RealTestAliceBob, self).setUp()
        self.inotify = magic_folder.get_inotify_module()
        return d
