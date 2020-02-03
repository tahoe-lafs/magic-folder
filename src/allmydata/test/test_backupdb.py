
import os.path, time
from six.moves import cStringIO as StringIO
from twisted.trial import unittest

from allmydata.util import fileutil
from allmydata.util.encodingutil import listdir_unicode
from allmydata.scripts import backupdb
from .common_util import skip_if_cannot_represent_filename

class BackupDB(unittest.TestCase):
    def create(self, dbfile):
        stderr = StringIO()
        bdb = backupdb.get_backupdb(dbfile, stderr=stderr)
        self.failUnless(bdb, "unable to create backupdb from %r" % (dbfile,))
        return bdb

    def test_basic(self):
        self.basedir = basedir = os.path.join("backupdb", "create")
        fileutil.make_dirs(basedir)
        dbfile = os.path.join(basedir, "dbfile")
        bdb = self.create(dbfile)
        self.failUnlessEqual(bdb.VERSION, 2)

    def test_upgrade_v1_v2(self):
        self.basedir = basedir = os.path.join("backupdb", "upgrade_v1_v2")
        fileutil.make_dirs(basedir)
        dbfile = os.path.join(basedir, "dbfile")
        stderr = StringIO()
        created = backupdb.get_backupdb(dbfile, stderr=stderr,
                                        create_version=(backupdb.SCHEMA_v1, 1),
                                        just_create=True)
        self.failUnless(created, "unable to create v1 backupdb")
        # now we should have a v1 database on disk
        bdb = self.create(dbfile)
        self.failUnlessEqual(bdb.VERSION, 2)

    def test_fail(self):
        self.basedir = basedir = os.path.join("backupdb", "fail")
        fileutil.make_dirs(basedir)

        # put a non-DB file in the way
        not_a_db = ("I do not look like a sqlite database\n" +
                    "I'M NOT" * 1000) # OS-X sqlite-2.3.2 takes some convincing
        self.writeto("not-a-database", not_a_db)
        stderr_f = StringIO()
        bdb = backupdb.get_backupdb(os.path.join(basedir, "not-a-database"),
                                    stderr_f)
        self.failUnlessEqual(bdb, None)
        stderr = stderr_f.getvalue()
        self.failUnlessIn("backupdb file is unusable", stderr)
        # sqlite-3.19.3 says "file is encrypted or is not a database"
        # sqlite-3.20.0 says "file is not a database"
        self.failUnlessIn("is not a database", stderr)

        # put a directory in the way, to exercise a different error path
        where = os.path.join(basedir, "roadblock-dir")
        fileutil.make_dirs(where)
        stderr_f = StringIO()
        bdb = backupdb.get_backupdb(where, stderr_f)
        self.failUnlessEqual(bdb, None)
        stderr = stderr_f.getvalue()
        self.failUnlessIn("Unable to create/open backupdb file %s" % (where,), stderr)
        self.failUnlessIn("unable to open database file", stderr)


    def writeto(self, filename, data):
        fn = os.path.join(self.basedir, unicode(filename))
        parentdir = os.path.dirname(fn)
        fileutil.make_dirs(parentdir)
        fileutil.write(fn, data)
        return fn

    def test_check(self):
        self.basedir = basedir = os.path.join("backupdb", "check")
        fileutil.make_dirs(basedir)
        dbfile = os.path.join(basedir, "dbfile")
        bdb = self.create(dbfile)

        foo_fn = self.writeto("foo.txt", "foo.txt")
        blah_fn = self.writeto("bar/blah.txt", "blah.txt")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("foo-cap")

        r = bdb.check_file(blah_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("blah-cap")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), "foo-cap")
        self.failUnlessEqual(type(r.was_uploaded()), str)
        self.failUnlessEqual(r.should_check(), False)

        time.sleep(1.0) # make sure the timestamp changes
        self.writeto("foo.txt", "NEW")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("new-cap")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), "new-cap")
        self.failUnlessEqual(r.should_check(), False)
        # if we spontaneously decide to upload it anyways, nothing should
        # break
        r.did_upload("new-cap")

        r = bdb.check_file(foo_fn, use_timestamps=False)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("new-cap")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), "new-cap")
        self.failUnlessEqual(r.should_check(), False)

        bdb.NO_CHECK_BEFORE = 0
        bdb.ALWAYS_CHECK_AFTER = 0.1

        r = bdb.check_file(blah_fn)
        self.failUnlessEqual(r.was_uploaded(), "blah-cap")
        self.failUnlessEqual(r.should_check(), True)
        r.did_check_healthy("results") # we know they're ignored for now

        bdb.NO_CHECK_BEFORE = 200
        bdb.ALWAYS_CHECK_AFTER = 400

        r = bdb.check_file(blah_fn)
        self.failUnlessEqual(r.was_uploaded(), "blah-cap")
        self.failUnlessEqual(r.should_check(), False)

        os.unlink(os.path.join(basedir, "foo.txt"))
        fileutil.make_dirs(os.path.join(basedir, "foo.txt")) # file becomes dir
        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), False)

    def test_wrong_version(self):
        self.basedir = basedir = os.path.join("backupdb", "wrong_version")
        fileutil.make_dirs(basedir)

        where = os.path.join(basedir, "tooold.db")
        bdb = self.create(where)
        # reach into the DB and make it old
        bdb.cursor.execute("UPDATE version SET version=0")
        bdb.connection.commit()

        # now the next time we open the database, it should be an unusable
        # version
        stderr_f = StringIO()
        bdb = backupdb.get_backupdb(where, stderr_f)
        self.failUnlessEqual(bdb, None)
        stderr = stderr_f.getvalue()
        self.failUnlessEqual(stderr.strip(),
                             "Unable to handle backupdb version 0")

    def test_directory(self):
        self.basedir = basedir = os.path.join("backupdb", "directory")
        fileutil.make_dirs(basedir)
        dbfile = os.path.join(basedir, "dbfile")
        bdb = self.create(dbfile)

        contents = {u"file1": "URI:CHK:blah1",
                    u"file2": "URI:CHK:blah2",
                    u"dir1": "URI:DIR2-CHK:baz2"}
        r = bdb.check_directory(contents)
        self.failUnless(isinstance(r, backupdb.DirectoryResult))
        self.failIf(r.was_created())
        dircap = "URI:DIR2-CHK:foo1"
        r.did_create(dircap)

        r = bdb.check_directory(contents)
        self.failUnless(r.was_created())
        self.failUnlessEqual(r.was_created(), dircap)
        self.failUnlessEqual(r.should_check(), False)

        # if we spontaneously decide to upload it anyways, nothing should
        # break
        r.did_create(dircap)
        r = bdb.check_directory(contents)
        self.failUnless(r.was_created())
        self.failUnlessEqual(r.was_created(), dircap)
        self.failUnlessEqual(type(r.was_created()), str)
        self.failUnlessEqual(r.should_check(), False)

        bdb.NO_CHECK_BEFORE = 0
        bdb.ALWAYS_CHECK_AFTER = 0.1
        time.sleep(1.0)

        r = bdb.check_directory(contents)
        self.failUnless(r.was_created())
        self.failUnlessEqual(r.was_created(), dircap)
        self.failUnlessEqual(r.should_check(), True)
        r.did_check_healthy("results")

        bdb.NO_CHECK_BEFORE = 200
        bdb.ALWAYS_CHECK_AFTER = 400

        r = bdb.check_directory(contents)
        self.failUnless(r.was_created())
        self.failUnlessEqual(r.was_created(), dircap)
        self.failUnlessEqual(r.should_check(), False)


        contents2 = {u"file1": "URI:CHK:blah1",
                     u"dir1": "URI:DIR2-CHK:baz2"}
        r = bdb.check_directory(contents2)
        self.failIf(r.was_created())

        contents3 = {u"file1": "URI:CHK:blah1",
                     u"file2": "URI:CHK:blah3",
                     u"dir1": "URI:DIR2-CHK:baz2"}
        r = bdb.check_directory(contents3)
        self.failIf(r.was_created())

    def test_unicode(self):
        skip_if_cannot_represent_filename(u"f\u00f6\u00f6.txt")
        skip_if_cannot_represent_filename(u"b\u00e5r.txt")

        self.basedir = basedir = os.path.join("backupdb", "unicode")
        fileutil.make_dirs(basedir)
        dbfile = os.path.join(basedir, "dbfile")
        bdb = self.create(dbfile)

        self.writeto(u"f\u00f6\u00f6.txt", "foo.txt")
        files = [fn for fn in listdir_unicode(unicode(basedir)) if fn.endswith(".txt")]
        self.failUnlessEqual(len(files), 1)
        foo_fn = os.path.join(basedir, files[0])
        #print foo_fn, type(foo_fn)

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("foo-cap")

        r = bdb.check_file(foo_fn)
        self.failUnlessEqual(r.was_uploaded(), "foo-cap")
        self.failUnlessEqual(r.should_check(), False)

        bar_fn = self.writeto(u"b\u00e5r.txt", "bar.txt")
        #print bar_fn, type(bar_fn)

        r = bdb.check_file(bar_fn)
        self.failUnlessEqual(r.was_uploaded(), False)
        r.did_upload("bar-cap")

        r = bdb.check_file(bar_fn)
        self.failUnlessEqual(r.was_uploaded(), "bar-cap")
        self.failUnlessEqual(r.should_check(), False)

