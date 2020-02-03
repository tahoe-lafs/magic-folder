from __future__ import print_function


def foo(): pass # keep the line number constant

import binascii
import six
import hashlib
import os, time, sys
import yaml

from six.moves import StringIO
from datetime import timedelta
from twisted.trial import unittest
from twisted.internet import defer, reactor
from twisted.python.failure import Failure
from twisted.python import log

from allmydata.util import base32, idlib, humanreadable, mathutil, hashutil
from allmydata.util import assertutil, fileutil, deferredutil, abbreviate
from allmydata.util import limiter, time_format, pollmixin, cachedir
from allmydata.util import statistics, dictutil, pipeline, yamlutil
from allmydata.util import log as tahoe_log
from allmydata.util.spans import Spans, overlap, DataSpans
from allmydata.util.fileutil import EncryptedTemporaryFile
from allmydata.test.common_util import ReallyEqualMixin, TimezoneMixin

if six.PY3:
    long = int


def sha256(data):
    """
    :param bytes data: data to hash

    :returns: a hex-encoded SHA256 hash of the data
    """
    return binascii.hexlify(hashlib.sha256(data).digest())


class Base32(unittest.TestCase):
    def test_b2a_matches_Pythons(self):
        import base64
        y = "\x12\x34\x45\x67\x89\x0a\xbc\xde\xf0"
        x = base64.b32encode(y)
        while x and x[-1] == '=':
            x = x[:-1]
        x = x.lower()
        self.failUnlessEqual(base32.b2a(y), x)
    def test_b2a(self):
        self.failUnlessEqual(base32.b2a("\x12\x34"), "ci2a")
    def test_b2a_or_none(self):
        self.failUnlessEqual(base32.b2a_or_none(None), None)
        self.failUnlessEqual(base32.b2a_or_none("\x12\x34"), "ci2a")
    def test_a2b(self):
        self.failUnlessEqual(base32.a2b("ci2a"), "\x12\x34")
        self.failUnlessRaises(AssertionError, base32.a2b, "b0gus")

class IDLib(unittest.TestCase):
    def test_nodeid_b2a(self):
        self.failUnlessEqual(idlib.nodeid_b2a("\x00"*20), "a"*32)

class NoArgumentException(Exception):
    def __init__(self):
        pass

class HumanReadable(unittest.TestCase):
    def test_repr(self):
        hr = humanreadable.hr
        self.failUnlessEqual(hr(foo), "<foo() at test_util.py:4>")
        self.failUnlessEqual(hr(self.test_repr),
                             "<bound method HumanReadable.test_repr of <allmydata.test.test_util.HumanReadable testMethod=test_repr>>")
        self.failUnlessEqual(hr(long(1)), "1")
        self.failUnlessEqual(hr(10**40),
                             "100000000000000000...000000000000000000")
        self.failUnlessEqual(hr(self), "<allmydata.test.test_util.HumanReadable testMethod=test_repr>")
        self.failUnlessEqual(hr([1,2]), "[1, 2]")
        self.failUnlessEqual(hr({1:2}), "{1:2}")
        try:
            raise ValueError
        except Exception as e:
            self.failUnless(
                hr(e) == "<ValueError: ()>" # python-2.4
                or hr(e) == "ValueError()") # python-2.5
        try:
            raise ValueError("oops")
        except Exception as e:
            self.failUnless(
                hr(e) == "<ValueError: 'oops'>" # python-2.4
                or hr(e) == "ValueError('oops',)") # python-2.5
        try:
            raise NoArgumentException
        except Exception as e:
            self.failUnless(
                hr(e) == "<NoArgumentException>" # python-2.4
                or hr(e) == "NoArgumentException()") # python-2.5

    def test_abbrev_time_1s(self):
        diff = timedelta(seconds=1)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('1 second ago', s)

    def test_abbrev_time_25s(self):
        diff = timedelta(seconds=25)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('25 seconds ago', s)

    def test_abbrev_time_future_5_minutes(self):
        diff = timedelta(minutes=-5)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('5 minutes in the future', s)

    def test_abbrev_time_hours(self):
        diff = timedelta(hours=4)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('4 hours ago', s)

    def test_abbrev_time_day(self):
        diff = timedelta(hours=49)  # must be more than 2 days
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('2 days ago', s)

    def test_abbrev_time_month(self):
        diff = timedelta(days=91)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('3 months ago', s)

    def test_abbrev_time_year(self):
        diff = timedelta(weeks=(5 * 52) + 1)
        s = abbreviate.abbreviate_time(diff)
        self.assertEqual('5 years ago', s)



class MyList(list):
    pass

class Math(unittest.TestCase):
    def test_div_ceil(self):
        f = mathutil.div_ceil
        self.failUnlessEqual(f(0, 1), 0)
        self.failUnlessEqual(f(0, 2), 0)
        self.failUnlessEqual(f(0, 3), 0)
        self.failUnlessEqual(f(1, 3), 1)
        self.failUnlessEqual(f(2, 3), 1)
        self.failUnlessEqual(f(3, 3), 1)
        self.failUnlessEqual(f(4, 3), 2)
        self.failUnlessEqual(f(5, 3), 2)
        self.failUnlessEqual(f(6, 3), 2)
        self.failUnlessEqual(f(7, 3), 3)

    def test_next_multiple(self):
        f = mathutil.next_multiple
        self.failUnlessEqual(f(5, 1), 5)
        self.failUnlessEqual(f(5, 2), 6)
        self.failUnlessEqual(f(5, 3), 6)
        self.failUnlessEqual(f(5, 4), 8)
        self.failUnlessEqual(f(5, 5), 5)
        self.failUnlessEqual(f(5, 6), 6)
        self.failUnlessEqual(f(32, 1), 32)
        self.failUnlessEqual(f(32, 2), 32)
        self.failUnlessEqual(f(32, 3), 33)
        self.failUnlessEqual(f(32, 4), 32)
        self.failUnlessEqual(f(32, 5), 35)
        self.failUnlessEqual(f(32, 6), 36)
        self.failUnlessEqual(f(32, 7), 35)
        self.failUnlessEqual(f(32, 8), 32)
        self.failUnlessEqual(f(32, 9), 36)
        self.failUnlessEqual(f(32, 10), 40)
        self.failUnlessEqual(f(32, 11), 33)
        self.failUnlessEqual(f(32, 12), 36)
        self.failUnlessEqual(f(32, 13), 39)
        self.failUnlessEqual(f(32, 14), 42)
        self.failUnlessEqual(f(32, 15), 45)
        self.failUnlessEqual(f(32, 16), 32)
        self.failUnlessEqual(f(32, 17), 34)
        self.failUnlessEqual(f(32, 18), 36)
        self.failUnlessEqual(f(32, 589), 589)

    def test_pad_size(self):
        f = mathutil.pad_size
        self.failUnlessEqual(f(0, 4), 0)
        self.failUnlessEqual(f(1, 4), 3)
        self.failUnlessEqual(f(2, 4), 2)
        self.failUnlessEqual(f(3, 4), 1)
        self.failUnlessEqual(f(4, 4), 0)
        self.failUnlessEqual(f(5, 4), 3)

    def test_is_power_of_k(self):
        f = mathutil.is_power_of_k
        for i in range(1, 100):
            if i in (1, 2, 4, 8, 16, 32, 64):
                self.failUnless(f(i, 2), "but %d *is* a power of 2" % i)
            else:
                self.failIf(f(i, 2), "but %d is *not* a power of 2" % i)
        for i in range(1, 100):
            if i in (1, 3, 9, 27, 81):
                self.failUnless(f(i, 3), "but %d *is* a power of 3" % i)
            else:
                self.failIf(f(i, 3), "but %d is *not* a power of 3" % i)

    def test_next_power_of_k(self):
        f = mathutil.next_power_of_k
        self.failUnlessEqual(f(0,2), 1)
        self.failUnlessEqual(f(1,2), 1)
        self.failUnlessEqual(f(2,2), 2)
        self.failUnlessEqual(f(3,2), 4)
        self.failUnlessEqual(f(4,2), 4)
        for i in range(5, 8): self.failUnlessEqual(f(i,2), 8, "%d" % i)
        for i in range(9, 16): self.failUnlessEqual(f(i,2), 16, "%d" % i)
        for i in range(17, 32): self.failUnlessEqual(f(i,2), 32, "%d" % i)
        for i in range(33, 64): self.failUnlessEqual(f(i,2), 64, "%d" % i)
        for i in range(65, 100): self.failUnlessEqual(f(i,2), 128, "%d" % i)

        self.failUnlessEqual(f(0,3), 1)
        self.failUnlessEqual(f(1,3), 1)
        self.failUnlessEqual(f(2,3), 3)
        self.failUnlessEqual(f(3,3), 3)
        for i in range(4, 9): self.failUnlessEqual(f(i,3), 9, "%d" % i)
        for i in range(10, 27): self.failUnlessEqual(f(i,3), 27, "%d" % i)
        for i in range(28, 81): self.failUnlessEqual(f(i,3), 81, "%d" % i)
        for i in range(82, 200): self.failUnlessEqual(f(i,3), 243, "%d" % i)

    def test_ave(self):
        f = mathutil.ave
        self.failUnlessEqual(f([1,2,3]), 2)
        self.failUnlessEqual(f([0,0,0,4]), 1)
        self.failUnlessAlmostEqual(f([0.0, 1.0, 1.0]), .666666666666)

    def test_round_sigfigs(self):
        f = mathutil.round_sigfigs
        self.failUnlessEqual(f(22.0/3, 4), 7.3330000000000002)

class Statistics(unittest.TestCase):
    def should_assert(self, msg, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
            self.fail(msg)
        except AssertionError:
            pass

    def failUnlessListEqual(self, a, b, msg = None):
        self.failUnlessEqual(len(a), len(b))
        for i in range(len(a)):
            self.failUnlessEqual(a[i], b[i], msg)

    def failUnlessListAlmostEqual(self, a, b, places = 7, msg = None):
        self.failUnlessEqual(len(a), len(b))
        for i in range(len(a)):
            self.failUnlessAlmostEqual(a[i], b[i], places, msg)

    def test_binomial_coeff(self):
        f = statistics.binomial_coeff
        self.failUnlessEqual(f(20, 0), 1)
        self.failUnlessEqual(f(20, 1), 20)
        self.failUnlessEqual(f(20, 2), 190)
        self.failUnlessEqual(f(20, 8), f(20, 12))
        self.should_assert("Should assert if n < k", f, 2, 3)

    def test_binomial_distribution_pmf(self):
        f = statistics.binomial_distribution_pmf

        pmf_comp = f(2, .1)
        pmf_stat = [0.81, 0.18, 0.01]
        self.failUnlessListAlmostEqual(pmf_comp, pmf_stat)

        # Summing across a PMF should give the total probability 1
        self.failUnlessAlmostEqual(sum(pmf_comp), 1)
        self.should_assert("Should assert if not 0<=p<=1", f, 1, -1)
        self.should_assert("Should assert if n < 1", f, 0, .1)

        out = StringIO()
        statistics.print_pmf(pmf_comp, out=out)
        lines = out.getvalue().splitlines()
        self.failUnlessEqual(lines[0], "i=0: 0.81")
        self.failUnlessEqual(lines[1], "i=1: 0.18")
        self.failUnlessEqual(lines[2], "i=2: 0.01")

    def test_survival_pmf(self):
        f = statistics.survival_pmf
        # Cross-check binomial-distribution method against convolution
        # method.
        p_list = [.9999] * 100 + [.99] * 50 + [.8] * 20
        pmf1 = statistics.survival_pmf_via_conv(p_list)
        pmf2 = statistics.survival_pmf_via_bd(p_list)
        self.failUnlessListAlmostEqual(pmf1, pmf2)
        self.failUnlessTrue(statistics.valid_pmf(pmf1))
        self.should_assert("Should assert if p_i > 1", f, [1.1]);
        self.should_assert("Should assert if p_i < 0", f, [-.1]);

    def test_repair_count_pmf(self):
        survival_pmf = statistics.binomial_distribution_pmf(5, .9)
        repair_pmf = statistics.repair_count_pmf(survival_pmf, 3)
        # repair_pmf[0] == sum(survival_pmf[0,1,2,5])
        # repair_pmf[1] == survival_pmf[4]
        # repair_pmf[2] = survival_pmf[3]
        self.failUnlessListAlmostEqual(repair_pmf,
                                       [0.00001 + 0.00045 + 0.0081 + 0.59049,
                                        .32805,
                                        .0729,
                                        0, 0, 0])

    def test_repair_cost(self):
        survival_pmf = statistics.binomial_distribution_pmf(5, .9)
        bwcost = statistics.bandwidth_cost_function
        cost = statistics.mean_repair_cost(bwcost, 1000,
                                           survival_pmf, 3, ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 558.90)
        cost = statistics.mean_repair_cost(bwcost, 1000,
                                           survival_pmf, 3, ul_dl_ratio=8.0)
        self.failUnlessAlmostEqual(cost, 1664.55)

        # I haven't manually checked the math beyond here -warner
        cost = statistics.eternal_repair_cost(bwcost, 1000,
                                              survival_pmf, 3,
                                              discount_rate=0, ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 65292.056074766246)
        cost = statistics.eternal_repair_cost(bwcost, 1000,
                                              survival_pmf, 3,
                                              discount_rate=0.05,
                                              ul_dl_ratio=1.0)
        self.failUnlessAlmostEqual(cost, 9133.6097158191551)

    def test_convolve(self):
        f = statistics.convolve
        v1 = [ 1, 2, 3 ]
        v2 = [ 4, 5, 6 ]
        v3 = [ 7, 8 ]
        v1v2result = [ 4, 13, 28, 27, 18 ]
        # Convolution is commutative
        r1 = f(v1, v2)
        r2 = f(v2, v1)
        self.failUnlessListEqual(r1, r2, "Convolution should be commutative")
        self.failUnlessListEqual(r1, v1v2result, "Didn't match known result")
        # Convolution is associative
        r1 = f(f(v1, v2), v3)
        r2 = f(v1, f(v2, v3))
        self.failUnlessListEqual(r1, r2, "Convolution should be associative")
        # Convolution is distributive
        r1 = f(v3, [ a + b for a, b in zip(v1, v2) ])
        tmp1 = f(v3, v1)
        tmp2 = f(v3, v2)
        r2 = [ a + b for a, b in zip(tmp1, tmp2) ]
        self.failUnlessListEqual(r1, r2, "Convolution should be distributive")
        # Convolution is scalar multiplication associative
        tmp1 = f(v1, v2)
        r1 = [ a * 4 for a in tmp1 ]
        tmp2 = [ a * 4 for a in v1 ]
        r2 = f(tmp2, v2)
        self.failUnlessListEqual(r1, r2, "Convolution should be scalar multiplication associative")

    def test_find_k(self):
        f = statistics.find_k
        g = statistics.pr_file_loss
        plist = [.9] * 10 + [.8] * 10 # N=20
        t = .0001
        k = f(plist, t)
        self.failUnlessEqual(k, 10)
        self.failUnless(g(plist, k) < t)

    def test_pr_file_loss(self):
        f = statistics.pr_file_loss
        plist = [.5] * 10
        self.failUnlessEqual(f(plist, 3), .0546875)

    def test_pr_backup_file_loss(self):
        f = statistics.pr_backup_file_loss
        plist = [.5] * 10
        self.failUnlessEqual(f(plist, .5, 3), .02734375)


class Asserts(unittest.TestCase):
    def should_assert(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except AssertionError as e:
            return str(e)
        except Exception as e:
            self.fail("assert failed with non-AssertionError: %s" % e)
        self.fail("assert was not caught")

    def should_not_assert(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except AssertionError as e:
            self.fail("assertion fired when it should not have: %s" % e)
        except Exception as e:
            self.fail("assertion (which shouldn't have failed) failed with non-AssertionError: %s" % e)
        return # we're happy


    def test_assert(self):
        f = assertutil._assert
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual(m, "'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("othermsg: 'message2' <type 'str'>", m)

    def test_precondition(self):
        f = assertutil.precondition
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual("precondition: 'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("precondition: 'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("precondition: othermsg: 'message2' <type 'str'>", m)

    def test_postcondition(self):
        f = assertutil.postcondition
        self.should_assert(f)
        self.should_assert(f, False)
        self.should_not_assert(f, True)

        m = self.should_assert(f, False, "message")
        self.failUnlessEqual("postcondition: 'message' <type 'str'>", m)
        m = self.should_assert(f, False, "message1", othermsg=12)
        self.failUnlessEqual("postcondition: 'message1' <type 'str'>, othermsg: 12 <type 'int'>", m)
        m = self.should_assert(f, False, othermsg="message2")
        self.failUnlessEqual("postcondition: othermsg: 'message2' <type 'str'>", m)

class FileUtil(ReallyEqualMixin, unittest.TestCase):
    def mkdir(self, basedir, path, mode=0o777):
        fn = os.path.join(basedir, path)
        fileutil.make_dirs(fn, mode)

    def touch(self, basedir, path, mode=None, data="touch\n"):
        fn = os.path.join(basedir, path)
        f = open(fn, "w")
        f.write(data)
        f.close()
        if mode is not None:
            os.chmod(fn, mode)

    def test_rm_dir(self):
        basedir = "util/FileUtil/test_rm_dir"
        fileutil.make_dirs(basedir)
        # create it again to test idempotency
        fileutil.make_dirs(basedir)
        d = os.path.join(basedir, "doomed")
        self.mkdir(d, "a/b")
        self.touch(d, "a/b/1.txt")
        self.touch(d, "a/b/2.txt", 0o444)
        self.touch(d, "a/b/3.txt", 0)
        self.mkdir(d, "a/c")
        self.touch(d, "a/c/1.txt")
        self.touch(d, "a/c/2.txt", 0o444)
        self.touch(d, "a/c/3.txt", 0)
        os.chmod(os.path.join(d, "a/c"), 0o444)
        self.mkdir(d, "a/d")
        self.touch(d, "a/d/1.txt")
        self.touch(d, "a/d/2.txt", 0o444)
        self.touch(d, "a/d/3.txt", 0)
        os.chmod(os.path.join(d, "a/d"), 0)

        fileutil.rm_dir(d)
        self.failIf(os.path.exists(d))
        # remove it again to test idempotency
        fileutil.rm_dir(d)

    def test_remove_if_possible(self):
        basedir = "util/FileUtil/test_remove_if_possible"
        fileutil.make_dirs(basedir)
        self.touch(basedir, "here")
        fn = os.path.join(basedir, "here")
        fileutil.remove_if_possible(fn)
        self.failIf(os.path.exists(fn))
        fileutil.remove_if_possible(fn) # should be idempotent
        fileutil.rm_dir(basedir)
        fileutil.remove_if_possible(fn) # should survive errors

    def test_write_atomically(self):
        basedir = "util/FileUtil/test_write_atomically"
        fileutil.make_dirs(basedir)
        fn = os.path.join(basedir, "here")
        fileutil.write_atomically(fn, "one")
        self.failUnlessEqual(fileutil.read(fn), "one")
        fileutil.write_atomically(fn, "two", mode="") # non-binary
        self.failUnlessEqual(fileutil.read(fn), "two")

    def test_rename(self):
        basedir = "util/FileUtil/test_rename"
        fileutil.make_dirs(basedir)
        self.touch(basedir, "here")
        fn = os.path.join(basedir, "here")
        fn2 = os.path.join(basedir, "there")
        fileutil.rename(fn, fn2)
        self.failIf(os.path.exists(fn))
        self.failUnless(os.path.exists(fn2))

    def test_rename_no_overwrite(self):
        workdir = fileutil.abspath_expanduser_unicode(u"test_rename_no_overwrite")
        fileutil.make_dirs(workdir)

        source_path = os.path.join(workdir, "source")
        dest_path   = os.path.join(workdir, "dest")

        # when neither file exists
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)

        # when only dest exists
        fileutil.write(dest_path,   "dest")
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)
        self.failUnlessEqual(fileutil.read(dest_path),   "dest")

        # when both exist
        fileutil.write(source_path, "source")
        self.failUnlessRaises(OSError, fileutil.rename_no_overwrite, source_path, dest_path)
        self.failUnlessEqual(fileutil.read(source_path), "source")
        self.failUnlessEqual(fileutil.read(dest_path),   "dest")

        # when only source exists
        os.remove(dest_path)
        fileutil.rename_no_overwrite(source_path, dest_path)
        self.failUnlessEqual(fileutil.read(dest_path), "source")
        self.failIf(os.path.exists(source_path))

    def test_replace_file(self):
        workdir = fileutil.abspath_expanduser_unicode(u"test_replace_file")
        fileutil.make_dirs(workdir)

        replaced_path    = os.path.join(workdir, "replaced")
        replacement_path = os.path.join(workdir, "replacement")

        # when none of the files exist
        self.failUnlessRaises(fileutil.ConflictError, fileutil.replace_file, replaced_path, replacement_path)

        # when only replaced exists
        fileutil.write(replaced_path,    "foo")
        self.failUnlessRaises(fileutil.ConflictError, fileutil.replace_file, replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), "foo")

        # when both replaced and replacement exist
        fileutil.write(replacement_path, "bar")
        fileutil.replace_file(replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), "bar")
        self.failIf(os.path.exists(replacement_path))

        # when only replacement exists
        os.remove(replaced_path)
        fileutil.write(replacement_path, "bar")
        fileutil.replace_file(replaced_path, replacement_path)
        self.failUnlessEqual(fileutil.read(replaced_path), "bar")
        self.failIf(os.path.exists(replacement_path))

    def test_du(self):
        basedir = "util/FileUtil/test_du"
        fileutil.make_dirs(basedir)
        d = os.path.join(basedir, "space-consuming")
        self.mkdir(d, "a/b")
        self.touch(d, "a/b/1.txt", data="a"*10)
        self.touch(d, "a/b/2.txt", data="b"*11)
        self.mkdir(d, "a/c")
        self.touch(d, "a/c/1.txt", data="c"*12)
        self.touch(d, "a/c/2.txt", data="d"*13)

        used = fileutil.du(basedir)
        self.failUnlessEqual(10+11+12+13, used)

    def test_abspath_expanduser_unicode(self):
        self.failUnlessRaises(AssertionError, fileutil.abspath_expanduser_unicode, "bytestring")

        saved_cwd = os.path.normpath(os.getcwdu())
        abspath_cwd = fileutil.abspath_expanduser_unicode(u".")
        abspath_cwd_notlong = fileutil.abspath_expanduser_unicode(u".", long_path=False)
        self.failUnless(isinstance(saved_cwd, unicode), saved_cwd)
        self.failUnless(isinstance(abspath_cwd, unicode), abspath_cwd)
        if sys.platform == "win32":
            self.failUnlessReallyEqual(abspath_cwd, fileutil.to_windows_long_path(saved_cwd))
        else:
            self.failUnlessReallyEqual(abspath_cwd, saved_cwd)
        self.failUnlessReallyEqual(abspath_cwd_notlong, saved_cwd)

        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"\\\\?\\foo"), u"\\\\?\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"\\\\.\\foo"), u"\\\\.\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"\\\\server\\foo"), u"\\\\?\\UNC\\server\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"C:\\foo"), u"\\\\?\\C:\\foo")
        self.failUnlessReallyEqual(fileutil.to_windows_long_path(u"C:\\foo/bar"), u"\\\\?\\C:\\foo\\bar")

        # adapted from <http://svn.python.org/view/python/branches/release26-maint/Lib/test/test_posixpath.py?view=markup&pathrev=78279#test_abspath>

        foo = fileutil.abspath_expanduser_unicode(u"foo")
        self.failUnless(foo.endswith(u"%sfoo" % (os.path.sep,)), foo)

        foobar = fileutil.abspath_expanduser_unicode(u"bar", base=foo)
        self.failUnless(foobar.endswith(u"%sfoo%sbar" % (os.path.sep, os.path.sep)), foobar)

        if sys.platform == "win32":
            # This is checking that a drive letter is added for a path without one.
            baz = fileutil.abspath_expanduser_unicode(u"\\baz")
            self.failUnless(baz.startswith(u"\\\\?\\"), baz)
            self.failUnlessReallyEqual(baz[5 :], u":\\baz")

            bar = fileutil.abspath_expanduser_unicode(u"\\bar", base=baz)
            self.failUnless(bar.startswith(u"\\\\?\\"), bar)
            self.failUnlessReallyEqual(bar[5 :], u":\\bar")
            # not u":\\baz\\bar", because \bar is absolute on the current drive.

            self.failUnlessReallyEqual(baz[4], bar[4])  # same drive

            baz_notlong = fileutil.abspath_expanduser_unicode(u"\\baz", long_path=False)
            self.failIf(baz_notlong.startswith(u"\\\\?\\"), baz_notlong)
            self.failUnlessReallyEqual(baz_notlong[1 :], u":\\baz")

            bar_notlong = fileutil.abspath_expanduser_unicode(u"\\bar", base=baz_notlong, long_path=False)
            self.failIf(bar_notlong.startswith(u"\\\\?\\"), bar_notlong)
            self.failUnlessReallyEqual(bar_notlong[1 :], u":\\bar")
            # not u":\\baz\\bar", because \bar is absolute on the current drive.

            self.failUnlessReallyEqual(baz_notlong[0], bar_notlong[0])  # same drive

        self.failIfIn(u"~", fileutil.abspath_expanduser_unicode(u"~"))
        self.failIfIn(u"~", fileutil.abspath_expanduser_unicode(u"~", long_path=False))

        cwds = ['cwd']
        try:
            cwds.append(u'\xe7w\xf0'.encode(sys.getfilesystemencoding()
                                            or 'ascii'))
        except UnicodeEncodeError:
            pass # the cwd can't be encoded -- test with ascii cwd only

        for cwd in cwds:
            try:
                os.mkdir(cwd)
                os.chdir(cwd)
                for upath in (u'', u'fuu', u'f\xf9\xf9', u'/fuu', u'U:\\', u'~'):
                    uabspath = fileutil.abspath_expanduser_unicode(upath)
                    self.failUnless(isinstance(uabspath, unicode), uabspath)

                    uabspath_notlong = fileutil.abspath_expanduser_unicode(upath, long_path=False)
                    self.failUnless(isinstance(uabspath_notlong, unicode), uabspath_notlong)
            finally:
                os.chdir(saved_cwd)

    def test_make_dirs_with_absolute_mode(self):
        if sys.platform == 'win32':
            raise unittest.SkipTest("Permissions don't work the same on windows.")

        workdir = fileutil.abspath_expanduser_unicode(u"test_make_dirs_with_absolute_mode")
        fileutil.make_dirs(workdir)
        abspath = fileutil.abspath_expanduser_unicode(u"a/b/c/d", base=workdir)
        fileutil.make_dirs_with_absolute_mode(workdir, abspath, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a", "b", "c", "d")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a", "b", "c")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a", "b")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(os.path.join(workdir, "a")).st_mode & 0o777
        self.failUnlessEqual(new_mode, 0o766)
        new_mode = os.stat(workdir).st_mode & 0o777
        self.failIfEqual(new_mode, 0o766)

    def test_create_long_path(self):
        """
        Even for paths with total length greater than 260 bytes,
        ``fileutil.abspath_expanduser_unicode`` produces a path on which other
        path-related APIs can operate.

        https://msdn.microsoft.com/en-us/library/windows/desktop/aa365247(v=vs.85).aspx
        documents certain Windows-specific path length limitations this test
        is specifically intended to demonstrate can be overcome.
        """
        workdir = u"test_create_long_path"
        fileutil.make_dirs(workdir)
        base_path = fileutil.abspath_expanduser_unicode(workdir)
        base_length = len(base_path)

        # Construct a path /just/ long enough to exercise the important case.
        # It would be nice if we could just use a seemingly globally valid
        # long file name (the `x...` portion) here - for example, a name 255
        # bytes long- and a previous version of this test did just that.
        # However, aufs imposes a 242 byte length limit on file names.  Most
        # other POSIX filesystems do allow names up to 255 bytes.  It's not
        # clear there's anything we can *do* about lower limits, though, and
        # POSIX.1-2017 (and earlier) only requires that the maximum be at
        # least 14 (!!!)  bytes.
        long_path = os.path.join(base_path, u'x' * (261 - base_length))

        def _cleanup():
            fileutil.remove(long_path)
        self.addCleanup(_cleanup)

        fileutil.write(long_path, "test")
        self.failUnless(os.path.exists(long_path))
        self.failUnlessEqual(fileutil.read(long_path), "test")
        _cleanup()
        self.failIf(os.path.exists(long_path))

    def _test_windows_expanduser(self, userprofile=None, homedrive=None, homepath=None):
        def call_windows_getenv(name):
            if name == u"USERPROFILE": return userprofile
            if name == u"HOMEDRIVE":   return homedrive
            if name == u"HOMEPATH":    return homepath
            self.fail("unexpected argument to call_windows_getenv")
        self.patch(fileutil, 'windows_getenv', call_windows_getenv)

        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"~"), os.path.join(u"C:", u"\\Documents and Settings\\\u0100"))
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"~\\foo"), os.path.join(u"C:", u"\\Documents and Settings\\\u0100", u"foo"))
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"~/foo"), os.path.join(u"C:", u"\\Documents and Settings\\\u0100", u"foo"))
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"a"), u"a")
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"a~"), u"a~")
        self.failUnlessReallyEqual(fileutil.windows_expanduser(u"a\\~\\foo"), u"a\\~\\foo")

    def test_windows_expanduser_xp(self):
        return self._test_windows_expanduser(homedrive=u"C:", homepath=u"\\Documents and Settings\\\u0100")

    def test_windows_expanduser_win7(self):
        return self._test_windows_expanduser(userprofile=os.path.join(u"C:", u"\\Documents and Settings\\\u0100"))

    def test_disk_stats(self):
        avail = fileutil.get_available_space('.', 2**14)
        if avail == 0:
            raise unittest.SkipTest("This test will spuriously fail there is no disk space left.")

        disk = fileutil.get_disk_stats('.', 2**13)
        self.failUnless(disk['total'] > 0, disk['total'])
        # we tolerate used==0 for a Travis-CI bug, see #2290
        self.failUnless(disk['used'] >= 0, disk['used'])
        self.failUnless(disk['free_for_root'] > 0, disk['free_for_root'])
        self.failUnless(disk['free_for_nonroot'] > 0, disk['free_for_nonroot'])
        self.failUnless(disk['avail'] > 0, disk['avail'])

    def test_disk_stats_avail_nonnegative(self):
        # This test will spuriously fail if you have more than 2^128
        # bytes of available space on your filesystem.
        disk = fileutil.get_disk_stats('.', 2**128)
        self.failUnlessEqual(disk['avail'], 0)

    def test_get_pathinfo(self):
        basedir = "util/FileUtil/test_get_pathinfo"
        fileutil.make_dirs(basedir)

        # create a directory
        self.mkdir(basedir, "a")
        dirinfo = fileutil.get_pathinfo(basedir)
        self.failUnlessTrue(dirinfo.isdir)
        self.failUnlessTrue(dirinfo.exists)
        self.failUnlessFalse(dirinfo.isfile)
        self.failUnlessFalse(dirinfo.islink)

        # create a file
        f = os.path.join(basedir, "1.txt")
        fileutil.write(f, "a"*10)
        fileinfo = fileutil.get_pathinfo(f)
        self.failUnlessTrue(fileinfo.isfile)
        self.failUnlessTrue(fileinfo.exists)
        self.failUnlessFalse(fileinfo.isdir)
        self.failUnlessFalse(fileinfo.islink)
        self.failUnlessEqual(fileinfo.size, 10)

        # path at which nothing exists
        dnename = os.path.join(basedir, "doesnotexist")
        now_ns = fileutil.seconds_to_ns(time.time())
        dneinfo = fileutil.get_pathinfo(dnename, now_ns=now_ns)
        self.failUnlessFalse(dneinfo.exists)
        self.failUnlessFalse(dneinfo.isfile)
        self.failUnlessFalse(dneinfo.isdir)
        self.failUnlessFalse(dneinfo.islink)
        self.failUnlessEqual(dneinfo.size, None)
        self.failUnlessEqual(dneinfo.mtime_ns, now_ns)
        self.failUnlessEqual(dneinfo.ctime_ns, now_ns)

    def test_get_pathinfo_symlink(self):
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest("can't create symlinks on this platform")

        basedir = "util/FileUtil/test_get_pathinfo"
        fileutil.make_dirs(basedir)

        f = os.path.join(basedir, "1.txt")
        fileutil.write(f, "a"*10)

        # create a symlink pointing to 1.txt
        slname = os.path.join(basedir, "linkto1.txt")
        os.symlink(f, slname)
        symlinkinfo = fileutil.get_pathinfo(slname)
        self.failUnlessTrue(symlinkinfo.islink)
        self.failUnlessTrue(symlinkinfo.exists)
        self.failUnlessFalse(symlinkinfo.isfile)
        self.failUnlessFalse(symlinkinfo.isdir)

    def test_encrypted_tempfile(self):
        f = EncryptedTemporaryFile()
        f.write("foobar")
        f.close()


class PollMixinTests(unittest.TestCase):
    def setUp(self):
        self.pm = pollmixin.PollMixin()

    def test_PollMixin_True(self):
        d = self.pm.poll(check_f=lambda : True,
                         pollinterval=0.1)
        return d

    def test_PollMixin_False_then_True(self):
        i = iter([False, True])
        d = self.pm.poll(check_f=i.next,
                         pollinterval=0.1)
        return d

    def test_timeout(self):
        d = self.pm.poll(check_f=lambda: False,
                         pollinterval=0.01,
                         timeout=1)
        def _suc(res):
            self.fail("poll should have failed, not returned %s" % (res,))
        def _err(f):
            f.trap(pollmixin.TimeoutError)
            return None # success
        d.addCallbacks(_suc, _err)
        return d

class DeferredUtilTests(unittest.TestCase, deferredutil.WaitForDelayedCallsMixin):
    def test_gather_results(self):
        d1 = defer.Deferred()
        d2 = defer.Deferred()
        res = deferredutil.gatherResults([d1, d2])
        d1.errback(ValueError("BAD"))
        def _callb(res):
            self.fail("Should have errbacked, not resulted in %s" % (res,))
        def _errb(thef):
            thef.trap(ValueError)
        res.addCallbacks(_callb, _errb)
        return res

    def test_success(self):
        d1, d2 = defer.Deferred(), defer.Deferred()
        good = []
        bad = []
        dlss = deferredutil.DeferredListShouldSucceed([d1,d2])
        dlss.addCallbacks(good.append, bad.append)
        d1.callback(1)
        d2.callback(2)
        self.failUnlessEqual(good, [[1,2]])
        self.failUnlessEqual(bad, [])

    def test_failure(self):
        d1, d2 = defer.Deferred(), defer.Deferred()
        good = []
        bad = []
        dlss = deferredutil.DeferredListShouldSucceed([d1,d2])
        dlss.addCallbacks(good.append, bad.append)
        d1.addErrback(lambda _ignore: None)
        d2.addErrback(lambda _ignore: None)
        d1.callback(1)
        d2.errback(ValueError())
        self.failUnlessEqual(good, [])
        self.failUnlessEqual(len(bad), 1)
        f = bad[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(ValueError))

    def test_wait_for_delayed_calls(self):
        """
        This tests that 'wait_for_delayed_calls' does in fact wait for a
        delayed call that is active when the test returns. If it didn't,
        Trial would report an unclean reactor error for this test.
        """
        def _trigger():
            #print "trigger"
            pass
        reactor.callLater(0.1, _trigger)

        d = defer.succeed(None)
        d.addBoth(self.wait_for_delayed_calls)
        return d

class HashUtilTests(unittest.TestCase):

    def test_random_key(self):
        k = hashutil.random_key()
        self.failUnlessEqual(len(k), hashutil.KEYLEN)

    def test_sha256d(self):
        h1 = hashutil.tagged_hash("tag1", "value")
        h2 = hashutil.tagged_hasher("tag1")
        h2.update("value")
        h2a = h2.digest()
        h2b = h2.digest()
        self.failUnlessEqual(h1, h2a)
        self.failUnlessEqual(h2a, h2b)

    def test_sha256d_truncated(self):
        h1 = hashutil.tagged_hash("tag1", "value", 16)
        h2 = hashutil.tagged_hasher("tag1", 16)
        h2.update("value")
        h2 = h2.digest()
        self.failUnlessEqual(len(h1), 16)
        self.failUnlessEqual(len(h2), 16)
        self.failUnlessEqual(h1, h2)

    def test_chk(self):
        h1 = hashutil.convergence_hash(3, 10, 1000, "data", "secret")
        h2 = hashutil.convergence_hasher(3, 10, 1000, "secret")
        h2.update("data")
        h2 = h2.digest()
        self.failUnlessEqual(h1, h2)

    def test_hashers(self):
        h1 = hashutil.block_hash("foo")
        h2 = hashutil.block_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.uri_extension_hash("foo")
        h2 = hashutil.uri_extension_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.plaintext_hash("foo")
        h2 = hashutil.plaintext_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.crypttext_hash("foo")
        h2 = hashutil.crypttext_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.crypttext_segment_hash("foo")
        h2 = hashutil.crypttext_segment_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

        h1 = hashutil.plaintext_segment_hash("foo")
        h2 = hashutil.plaintext_segment_hasher()
        h2.update("foo")
        self.failUnlessEqual(h1, h2.digest())

    def test_timing_safe_compare(self):
        self.failUnless(hashutil.timing_safe_compare("a", "a"))
        self.failUnless(hashutil.timing_safe_compare("ab", "ab"))
        self.failIf(hashutil.timing_safe_compare("a", "b"))
        self.failIf(hashutil.timing_safe_compare("a", "aa"))

    def _testknown(self, hashf, expected_a, *args):
        got = hashf(*args)
        got_a = base32.b2a(got)
        self.failUnlessEqual(got_a, expected_a)

    def test_known_answers(self):
        # assert backwards compatibility
        self._testknown(hashutil.storage_index_hash, "qb5igbhcc5esa6lwqorsy7e6am", "")
        self._testknown(hashutil.block_hash, "msjr5bh4evuh7fa3zw7uovixfbvlnstr5b65mrerwfnvjxig2jvq", "")
        self._testknown(hashutil.uri_extension_hash, "wthsu45q7zewac2mnivoaa4ulh5xvbzdmsbuyztq2a5fzxdrnkka", "")
        self._testknown(hashutil.plaintext_hash, "5lz5hwz3qj3af7n6e3arblw7xzutvnd3p3fjsngqjcb7utf3x3da", "")
        self._testknown(hashutil.crypttext_hash, "itdj6e4njtkoiavlrmxkvpreosscssklunhwtvxn6ggho4rkqwga", "")
        self._testknown(hashutil.crypttext_segment_hash, "aovy5aa7jej6ym5ikgwyoi4pxawnoj3wtaludjz7e2nb5xijb7aa", "")
        self._testknown(hashutil.plaintext_segment_hash, "4fdgf6qruaisyukhqcmoth4t3li6bkolbxvjy4awwcpprdtva7za", "")
        self._testknown(hashutil.convergence_hash, "3mo6ni7xweplycin6nowynw2we", 3, 10, 100, "", "converge")
        self._testknown(hashutil.my_renewal_secret_hash, "ujhr5k5f7ypkp67jkpx6jl4p47pyta7hu5m527cpcgvkafsefm6q", "")
        self._testknown(hashutil.my_cancel_secret_hash, "rjwzmafe2duixvqy6h47f5wfrokdziry6zhx4smew4cj6iocsfaa", "")
        self._testknown(hashutil.file_renewal_secret_hash, "hzshk2kf33gzbd5n3a6eszkf6q6o6kixmnag25pniusyaulqjnia", "", "si")
        self._testknown(hashutil.file_cancel_secret_hash, "bfciwvr6w7wcavsngxzxsxxaszj72dej54n4tu2idzp6b74g255q", "", "si")
        self._testknown(hashutil.bucket_renewal_secret_hash, "e7imrzgzaoashsncacvy3oysdd2m5yvtooo4gmj4mjlopsazmvuq", "", "\x00"*20)
        self._testknown(hashutil.bucket_cancel_secret_hash, "dvdujeyxeirj6uux6g7xcf4lvesk632aulwkzjar7srildvtqwma", "", "\x00"*20)
        self._testknown(hashutil.hmac, "c54ypfi6pevb3nvo6ba42jtglpkry2kbdopqsi7dgrm4r7tw5sra", "tag", "")
        self._testknown(hashutil.mutable_rwcap_key_hash, "6rvn2iqrghii5n4jbbwwqqsnqu", "iv", "wk")
        self._testknown(hashutil.ssk_writekey_hash, "ykpgmdbpgbb6yqz5oluw2q26ye", "")
        self._testknown(hashutil.ssk_write_enabler_master_hash, "izbfbfkoait4dummruol3gy2bnixrrrslgye6ycmkuyujnenzpia", "")
        self._testknown(hashutil.ssk_write_enabler_hash, "fuu2dvx7g6gqu5x22vfhtyed7p4pd47y5hgxbqzgrlyvxoev62tq", "wk", "\x00"*20)
        self._testknown(hashutil.ssk_pubkey_fingerprint_hash, "3opzw4hhm2sgncjx224qmt5ipqgagn7h5zivnfzqycvgqgmgz35q", "")
        self._testknown(hashutil.ssk_readkey_hash, "vugid4as6qbqgeq2xczvvcedai", "")
        self._testknown(hashutil.ssk_readkey_data_hash, "73wsaldnvdzqaf7v4pzbr2ae5a", "iv", "rk")
        self._testknown(hashutil.ssk_storage_index_hash, "j7icz6kigb6hxrej3tv4z7ayym", "")

        self._testknown(hashutil.permute_server_hash,
                        "kb4354zeeurpo3ze5e275wzbynm6hlap", # b32(expected)
                        "SI", # peer selection index == storage_index
                        base32.a2b("u33m4y7klhz3bypswqkozwetvabelhxt"), # seed
                        )

class Abbreviate(unittest.TestCase):
    def test_time(self):
        a = abbreviate.abbreviate_time
        self.failUnlessEqual(a(None), "unknown")
        self.failUnlessEqual(a(0), "0 seconds")
        self.failUnlessEqual(a(1), "1 second")
        self.failUnlessEqual(a(2), "2 seconds")
        self.failUnlessEqual(a(119), "119 seconds")
        MIN = 60
        self.failUnlessEqual(a(2*MIN), "2 minutes")
        self.failUnlessEqual(a(60*MIN), "60 minutes")
        self.failUnlessEqual(a(179*MIN), "179 minutes")
        HOUR = 60*MIN
        self.failUnlessEqual(a(180*MIN), "3 hours")
        self.failUnlessEqual(a(4*HOUR), "4 hours")
        DAY = 24*HOUR
        MONTH = 30*DAY
        self.failUnlessEqual(a(2*DAY), "2 days")
        self.failUnlessEqual(a(2*MONTH), "2 months")
        YEAR = 365*DAY
        self.failUnlessEqual(a(5*YEAR), "5 years")

    def test_space(self):
        tests_si = [(None, "unknown"),
                    (0, "0 B"),
                    (1, "1 B"),
                    (999, "999 B"),
                    (1000, "1000 B"),
                    (1023, "1023 B"),
                    (1024, "1.02 kB"),
                    (20*1000, "20.00 kB"),
                    (1024*1024, "1.05 MB"),
                    (1000*1000, "1.00 MB"),
                    (1000*1000*1000, "1.00 GB"),
                    (1000*1000*1000*1000, "1.00 TB"),
                    (1000*1000*1000*1000*1000, "1.00 PB"),
                    (1000*1000*1000*1000*1000*1000, "1.00 EB"),
                    (1234567890123456789, "1.23 EB"),
                    ]
        for (x, expected) in tests_si:
            got = abbreviate.abbreviate_space(x, SI=True)
            self.failUnlessEqual(got, expected)

        tests_base1024 = [(None, "unknown"),
                          (0, "0 B"),
                          (1, "1 B"),
                          (999, "999 B"),
                          (1000, "1000 B"),
                          (1023, "1023 B"),
                          (1024, "1.00 kiB"),
                          (20*1024, "20.00 kiB"),
                          (1000*1000, "976.56 kiB"),
                          (1024*1024, "1.00 MiB"),
                          (1024*1024*1024, "1.00 GiB"),
                          (1024*1024*1024*1024, "1.00 TiB"),
                          (1000*1000*1000*1000*1000, "909.49 TiB"),
                          (1024*1024*1024*1024*1024, "1.00 PiB"),
                          (1024*1024*1024*1024*1024*1024, "1.00 EiB"),
                          (1234567890123456789, "1.07 EiB"),
                    ]
        for (x, expected) in tests_base1024:
            got = abbreviate.abbreviate_space(x, SI=False)
            self.failUnlessEqual(got, expected)

        self.failUnlessEqual(abbreviate.abbreviate_space_both(1234567),
                             "(1.23 MB, 1.18 MiB)")

    def test_parse_space(self):
        p = abbreviate.parse_abbreviated_size
        self.failUnlessEqual(p(""), None)
        self.failUnlessEqual(p(None), None)
        self.failUnlessEqual(p("123"), 123)
        self.failUnlessEqual(p("123B"), 123)
        self.failUnlessEqual(p("2K"), 2000)
        self.failUnlessEqual(p("2kb"), 2000)
        self.failUnlessEqual(p("2KiB"), 2048)
        self.failUnlessEqual(p("10MB"), 10*1000*1000)
        self.failUnlessEqual(p("10MiB"), 10*1024*1024)
        self.failUnlessEqual(p("5G"), 5*1000*1000*1000)
        self.failUnlessEqual(p("4GiB"), 4*1024*1024*1024)
        self.failUnlessEqual(p("3TB"), 3*1000*1000*1000*1000)
        self.failUnlessEqual(p("3TiB"), 3*1024*1024*1024*1024)
        self.failUnlessEqual(p("6PB"), 6*1000*1000*1000*1000*1000)
        self.failUnlessEqual(p("6PiB"), 6*1024*1024*1024*1024*1024)
        self.failUnlessEqual(p("9EB"), 9*1000*1000*1000*1000*1000*1000)
        self.failUnlessEqual(p("9EiB"), 9*1024*1024*1024*1024*1024*1024)

        e = self.failUnlessRaises(ValueError, p, "12 cubits")
        self.failUnlessIn("12 cubits", str(e))
        e = self.failUnlessRaises(ValueError, p, "1 BB")
        self.failUnlessIn("1 BB", str(e))
        e = self.failUnlessRaises(ValueError, p, "fhtagn")
        self.failUnlessIn("fhtagn", str(e))

class Limiter(unittest.TestCase):

    def job(self, i, foo):
        self.calls.append( (i, foo) )
        self.simultaneous += 1
        self.peak_simultaneous = max(self.simultaneous, self.peak_simultaneous)
        d = defer.Deferred()
        def _done():
            self.simultaneous -= 1
            d.callback("done %d" % i)
        reactor.callLater(1.0, _done)
        return d

    def bad_job(self, i, foo):
        raise ValueError("bad_job %d" % i)

    def test_limiter(self):
        self.calls = []
        self.simultaneous = 0
        self.peak_simultaneous = 0
        l = limiter.ConcurrencyLimiter()
        dl = []
        for i in range(20):
            dl.append(l.add(self.job, i, foo=str(i)))
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _done(res):
            self.failUnlessEqual(self.simultaneous, 0)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
        d.addCallback(_done)
        return d

    def test_errors(self):
        self.calls = []
        self.simultaneous = 0
        self.peak_simultaneous = 0
        l = limiter.ConcurrencyLimiter()
        dl = []
        for i in range(20):
            dl.append(l.add(self.job, i, foo=str(i)))
        d2 = l.add(self.bad_job, 21, "21")
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _most_done(res):
            results = []
            for (success, result) in res:
                self.failUnlessEqual(success, True)
                results.append(result)
            results.sort()
            expected_results = ["done %d" % i for i in range(20)]
            expected_results.sort()
            self.failUnlessEqual(results, expected_results)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
            def _good(res):
                self.fail("should have failed, not got %s" % (res,))
            def _err(f):
                f.trap(ValueError)
                self.failUnless("bad_job 21" in str(f))
            d2.addCallbacks(_good, _err)
            return d2
        d.addCallback(_most_done)
        def _all_done(res):
            self.failUnlessEqual(self.simultaneous, 0)
            self.failUnless(self.peak_simultaneous <= 10)
            self.failUnlessEqual(len(self.calls), 20)
            for i in range(20):
                self.failUnless( (i, str(i)) in self.calls)
        d.addCallback(_all_done)
        return d

class TimeFormat(unittest.TestCase, TimezoneMixin):
    def test_epoch(self):
        return self._help_test_epoch()

    def test_epoch_in_London(self):
        # Europe/London is a particularly troublesome timezone.  Nowadays, its
        # offset from GMT is 0.  But in 1970, its offset from GMT was 1.
        # (Apparently in 1970 Britain had redefined standard time to be GMT+1
        # and stayed in standard time all year round, whereas today
        # Europe/London standard time is GMT and Europe/London Daylight
        # Savings Time is GMT+1.)  The current implementation of
        # time_format.iso_utc_time_to_localseconds() breaks if the timezone is
        # Europe/London.  (As soon as this unit test is done then I'll change
        # that implementation to something that works even in this case...)

        if not self.have_working_tzset():
            raise unittest.SkipTest("This test can't be run on a platform without time.tzset().")

        self.setTimezone("Europe/London")
        return self._help_test_epoch()

    def _help_test_epoch(self):
        origtzname = time.tzname
        s = time_format.iso_utc_time_to_seconds("1970-01-01T00:00:01")
        self.failUnlessEqual(s, 1.0)
        s = time_format.iso_utc_time_to_seconds("1970-01-01_00:00:01")
        self.failUnlessEqual(s, 1.0)
        s = time_format.iso_utc_time_to_seconds("1970-01-01 00:00:01")
        self.failUnlessEqual(s, 1.0)

        self.failUnlessEqual(time_format.iso_utc(1.0), "1970-01-01_00:00:01")
        self.failUnlessEqual(time_format.iso_utc(1.0, sep=" "),
                             "1970-01-01 00:00:01")

        now = time.time()
        isostr = time_format.iso_utc(now)
        timestamp = time_format.iso_utc_time_to_seconds(isostr)
        self.failUnlessEqual(int(timestamp), int(now))

        def my_time():
            return 1.0
        self.failUnlessEqual(time_format.iso_utc(t=my_time),
                             "1970-01-01_00:00:01")
        e = self.failUnlessRaises(ValueError,
                                  time_format.iso_utc_time_to_seconds,
                                  "invalid timestring")
        self.failUnless("not a complete ISO8601 timestamp" in str(e))
        s = time_format.iso_utc_time_to_seconds("1970-01-01_00:00:01.500")
        self.failUnlessEqual(s, 1.5)

        # Look for daylight-savings-related errors.
        thatmomentinmarch = time_format.iso_utc_time_to_seconds("2009-03-20 21:49:02.226536")
        self.failUnlessEqual(thatmomentinmarch, 1237585742.226536)
        self.failUnlessEqual(origtzname, time.tzname)

    def test_iso_utc(self):
        when = 1266760143.7841301
        out = time_format.iso_utc_date(when)
        self.failUnlessEqual(out, "2010-02-21")
        out = time_format.iso_utc_date(t=lambda: when)
        self.failUnlessEqual(out, "2010-02-21")
        out = time_format.iso_utc(when)
        self.failUnlessEqual(out, "2010-02-21_13:49:03.784130")
        out = time_format.iso_utc(when, sep="-")
        self.failUnlessEqual(out, "2010-02-21-13:49:03.784130")

    def test_parse_duration(self):
        p = time_format.parse_duration
        DAY = 24*60*60
        self.failUnlessEqual(p("1 day"), DAY)
        self.failUnlessEqual(p("2 days"), 2*DAY)
        self.failUnlessEqual(p("3 months"), 3*31*DAY)
        self.failUnlessEqual(p("4 mo"), 4*31*DAY)
        self.failUnlessEqual(p("5 years"), 5*365*DAY)
        e = self.failUnlessRaises(ValueError, p, "123")
        self.failUnlessIn("no unit (like day, month, or year) in '123'",
                          str(e))

    def test_parse_date(self):
        self.failUnlessEqual(time_format.parse_date("2010-02-21"), 1266710400)

    def test_format_time(self):
        self.failUnlessEqual(time_format.format_time(time.gmtime(0)), '1970-01-01 00:00:00')
        self.failUnlessEqual(time_format.format_time(time.gmtime(60)), '1970-01-01 00:01:00')
        self.failUnlessEqual(time_format.format_time(time.gmtime(60*60)), '1970-01-01 01:00:00')
        seconds_per_day = 60*60*24
        leap_years_1970_to_2014_inclusive = ((2012 - 1968) // 4)
        self.failUnlessEqual(time_format.format_time(time.gmtime(seconds_per_day*((2015 - 1970)*365+leap_years_1970_to_2014_inclusive))), '2015-01-01 00:00:00')

    def test_format_time_y2038(self):
        seconds_per_day = 60*60*24
        leap_years_1970_to_2047_inclusive = ((2044 - 1968) // 4)
        t = (seconds_per_day*
             ((2048 - 1970)*365 + leap_years_1970_to_2047_inclusive))
        try:
            gm_t = time.gmtime(t)
        except ValueError:
            raise unittest.SkipTest("Note: this system cannot handle dates after 2037.")
        self.failUnlessEqual(time_format.format_time(gm_t),
                             '2048-01-01 00:00:00')

    def test_format_delta(self):
        time_1 = 1389812723
        time_5s_delta = 1389812728
        time_28m7s_delta = 1389814410
        time_1h_delta = 1389816323
        time_1d21h46m49s_delta = 1389977532

        self.failUnlessEqual(
            time_format.format_delta(time_1, time_1), '0s')

        self.failUnlessEqual(
            time_format.format_delta(time_1, time_5s_delta), '5s')
        self.failUnlessEqual(
            time_format.format_delta(time_1, time_28m7s_delta), '28m 7s')
        self.failUnlessEqual(
            time_format.format_delta(time_1, time_1h_delta), '1h 0m 0s')
        self.failUnlessEqual(
            time_format.format_delta(time_1, time_1d21h46m49s_delta), '1d 21h 46m 49s')

        self.failUnlessEqual(
            time_format.format_delta(time_1d21h46m49s_delta, time_1), '-')

        # time_1 with a decimal fraction will make the delta 1s less
        time_1decimal = 1389812723.383963

        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_5s_delta), '4s')
        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_28m7s_delta), '28m 6s')
        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_1h_delta), '59m 59s')
        self.failUnlessEqual(
            time_format.format_delta(time_1decimal, time_1d21h46m49s_delta), '1d 21h 46m 48s')

class CacheDir(unittest.TestCase):
    def test_basic(self):
        basedir = "test_util/CacheDir/test_basic"

        def _failIfExists(name):
            absfn = os.path.join(basedir, name)
            self.failIf(os.path.exists(absfn),
                        "%s exists but it shouldn't" % absfn)

        def _failUnlessExists(name):
            absfn = os.path.join(basedir, name)
            self.failUnless(os.path.exists(absfn),
                            "%s doesn't exist but it should" % absfn)

        cdm = cachedir.CacheDirectoryManager(basedir)
        a = cdm.get_file("a")
        b = cdm.get_file("b")
        c = cdm.get_file("c")
        f = open(a.get_filename(), "wb"); f.write("hi"); f.close(); del f
        f = open(b.get_filename(), "wb"); f.write("hi"); f.close(); del f
        f = open(c.get_filename(), "wb"); f.write("hi"); f.close(); del f

        _failUnlessExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        cdm.check()

        _failUnlessExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        del a
        # this file won't be deleted yet, because it isn't old enough
        cdm.check()
        _failUnlessExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        # we change the definition of "old" to make everything old
        cdm.old = -10

        cdm.check()
        _failIfExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        cdm.old = 60*60

        del b

        cdm.check()
        _failIfExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")

        b2 = cdm.get_file("b")

        cdm.check()
        _failIfExists("a")
        _failUnlessExists("b")
        _failUnlessExists("c")
        del b2

ctr = [0]
class EqButNotIs(object):
    def __init__(self, x):
        self.x = x
        self.hash = ctr[0]
        ctr[0] += 1
    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.x,)
    def __hash__(self):
        return self.hash
    def __le__(self, other):
        return self.x <= other
    def __lt__(self, other):
        return self.x < other
    def __ge__(self, other):
        return self.x >= other
    def __gt__(self, other):
        return self.x > other
    def __ne__(self, other):
        return self.x != other
    def __eq__(self, other):
        return self.x == other

class DictUtil(unittest.TestCase):
    def test_dict_of_sets(self):
        ds = dictutil.DictOfSets()
        ds.add(1, "a")
        ds.add(2, "b")
        ds.add(2, "b")
        ds.add(2, "c")
        self.failUnlessEqual(ds[1], set(["a"]))
        self.failUnlessEqual(ds[2], set(["b", "c"]))
        ds.discard(3, "d") # should not raise an exception
        ds.discard(2, "b")
        self.failUnlessEqual(ds[2], set(["c"]))
        ds.discard(2, "c")
        self.failIf(2 in ds)

        ds.add(3, "f")
        ds2 = dictutil.DictOfSets()
        ds2.add(3, "f")
        ds2.add(3, "g")
        ds2.add(4, "h")
        ds.update(ds2)
        self.failUnlessEqual(ds[1], set(["a"]))
        self.failUnlessEqual(ds[3], set(["f", "g"]))
        self.failUnlessEqual(ds[4], set(["h"]))

    def test_auxdict(self):
        d = dictutil.AuxValueDict()
        # we put the serialized form in the auxdata
        d.set_with_aux("key", ("filecap", "metadata"), "serialized")

        self.failUnlessEqual(d.keys(), ["key"])
        self.failUnlessEqual(d["key"], ("filecap", "metadata"))
        self.failUnlessEqual(d.get_aux("key"), "serialized")
        def _get_missing(key):
            return d[key]
        self.failUnlessRaises(KeyError, _get_missing, "nonkey")
        self.failUnlessEqual(d.get("nonkey"), None)
        self.failUnlessEqual(d.get("nonkey", "nonvalue"), "nonvalue")
        self.failUnlessEqual(d.get_aux("nonkey"), None)
        self.failUnlessEqual(d.get_aux("nonkey", "nonvalue"), "nonvalue")

        d["key"] = ("filecap2", "metadata2")
        self.failUnlessEqual(d["key"], ("filecap2", "metadata2"))
        self.failUnlessEqual(d.get_aux("key"), None)

        d.set_with_aux("key2", "value2", "aux2")
        self.failUnlessEqual(sorted(d.keys()), ["key", "key2"])
        del d["key2"]
        self.failUnlessEqual(d.keys(), ["key"])
        self.failIf("key2" in d)
        self.failUnlessRaises(KeyError, _get_missing, "key2")
        self.failUnlessEqual(d.get("key2"), None)
        self.failUnlessEqual(d.get_aux("key2"), None)
        d["key2"] = "newvalue2"
        self.failUnlessEqual(d.get("key2"), "newvalue2")
        self.failUnlessEqual(d.get_aux("key2"), None)

        d = dictutil.AuxValueDict({1:2,3:4})
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict([ (1,2), (3,4) ])
        self.failUnlessEqual(sorted(d.keys()), [1,3])
        self.failUnlessEqual(d[1], 2)
        self.failUnlessEqual(d.get_aux(1), None)

        d = dictutil.AuxValueDict(one=1, two=2)
        self.failUnlessEqual(sorted(d.keys()), ["one","two"])
        self.failUnlessEqual(d["one"], 1)
        self.failUnlessEqual(d.get_aux("one"), None)

class Pipeline(unittest.TestCase):
    def pause(self, *args, **kwargs):
        d = defer.Deferred()
        self.calls.append( (d, args, kwargs) )
        return d

    def failUnlessCallsAre(self, expected):
        #print self.calls
        #print expected
        self.failUnlessEqual(len(self.calls), len(expected), self.calls)
        for i,c in enumerate(self.calls):
            self.failUnlessEqual(c[1:], expected[i], str(i))

    def test_basic(self):
        self.calls = []
        finished = []
        p = pipeline.Pipeline(100)

        d = p.flush() # fires immediately
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        finished = []

        d = p.add(10, self.pause, "one")
        # the call should start right away, and our return Deferred should
        # fire right away
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessCallsAre([ ( ("one",) , {} ) ])
        self.failUnlessEqual(p.gauge, 10)

        # pipeline: [one]

        finished = []
        d = p.add(20, self.pause, "two", kw=2)
        # pipeline: [one, two]

        # the call and the Deferred should fire right away
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessCallsAre([ ( ("one",) , {} ),
                                  ( ("two",) , {"kw": 2} ),
                                  ])
        self.failUnlessEqual(p.gauge, 30)

        self.calls[0][0].callback("one-result")
        # pipeline: [two]
        self.failUnlessEqual(p.gauge, 20)

        finished = []
        d = p.add(90, self.pause, "three", "posarg1")
        # pipeline: [two, three]
        flushed = []
        fd = p.flush()
        fd.addCallbacks(flushed.append, log.err)
        self.failUnlessEqual(flushed, [])

        # the call will be made right away, but the return Deferred will not,
        # because the pipeline is now full.
        d.addCallbacks(finished.append, log.err)
        self.failUnlessEqual(len(finished), 0)
        self.failUnlessCallsAre([ ( ("one",) , {} ),
                                  ( ("two",) , {"kw": 2} ),
                                  ( ("three", "posarg1"), {} ),
                                  ])
        self.failUnlessEqual(p.gauge, 110)

        self.failUnlessRaises(pipeline.SingleFileError, p.add, 10, self.pause)

        # retiring either call will unblock the pipeline, causing the #3
        # Deferred to fire
        self.calls[2][0].callback("three-result")
        # pipeline: [two]

        self.failUnlessEqual(len(finished), 1)
        self.failUnlessEqual(finished[0], None)
        self.failUnlessEqual(flushed, [])

        # retiring call#2 will finally allow the flush() Deferred to fire
        self.calls[1][0].callback("two-result")
        self.failUnlessEqual(len(flushed), 1)

    def test_errors(self):
        self.calls = []
        p = pipeline.Pipeline(100)

        d1 = p.add(200, self.pause, "one")
        d2 = p.flush()

        finished = []
        d1.addBoth(finished.append)
        self.failUnlessEqual(finished, [])

        flushed = []
        d2.addBoth(flushed.append)
        self.failUnlessEqual(flushed, [])

        self.calls[0][0].errback(ValueError("oops"))

        self.failUnlessEqual(len(finished), 1)
        f = finished[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        self.failUnlessIn("PipelineError", str(f.value))
        self.failUnlessIn("ValueError", str(f.value))
        r = repr(f.value)
        self.failUnless("ValueError" in r, r)
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        # now that the pipeline is in the failed state, any new calls will
        # fail immediately

        d3 = p.add(20, self.pause, "two")

        finished = []
        d3.addBoth(finished.append)
        self.failUnlessEqual(len(finished), 1)
        f = finished[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        r = repr(f.value)
        self.failUnless("ValueError" in r, r)
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        d4 = p.flush()
        flushed = []
        d4.addBoth(flushed.append)
        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

    def test_errors2(self):
        self.calls = []
        p = pipeline.Pipeline(100)

        d1 = p.add(10, self.pause, "one")
        d2 = p.add(20, self.pause, "two")
        d3 = p.add(30, self.pause, "three")
        d4 = p.flush()

        # one call fails, then the second one succeeds: make sure
        # ExpandableDeferredList tolerates the second one

        flushed = []
        d4.addBoth(flushed.append)
        self.failUnlessEqual(flushed, [])

        self.calls[0][0].errback(ValueError("oops"))
        self.failUnlessEqual(len(flushed), 1)
        f = flushed[0]
        self.failUnless(isinstance(f, Failure))
        self.failUnless(f.check(pipeline.PipelineError))
        f2 = f.value.error
        self.failUnless(f2.check(ValueError))

        self.calls[1][0].callback("two-result")
        self.calls[2][0].errback(ValueError("three-error"))

        del d1,d2,d3,d4

class SampleError(Exception):
    pass

class Log(unittest.TestCase):
    def test_err(self):
        try:
            raise SampleError("simple sample")
        except:
            f = Failure()
        tahoe_log.err(format="intentional sample error",
                      failure=f, level=tahoe_log.OPERATIONAL, umid="wO9UoQ")
        self.flushLoggedErrors(SampleError)


class SimpleSpans(object):
    # this is a simple+inefficient form of util.spans.Spans . We compare the
    # behavior of this reference model against the real (efficient) form.

    def __init__(self, _span_or_start=None, length=None):
        self._have = set()
        if length is not None:
            for i in range(_span_or_start, _span_or_start+length):
                self._have.add(i)
        elif _span_or_start:
            for (start,length) in _span_or_start:
                self.add(start, length)

    def add(self, start, length):
        for i in range(start, start+length):
            self._have.add(i)
        return self

    def remove(self, start, length):
        for i in range(start, start+length):
            self._have.discard(i)
        return self

    def each(self):
        return sorted(self._have)

    def __iter__(self):
        items = sorted(self._have)
        prevstart = None
        prevend = None
        for i in items:
            if prevstart is None:
                prevstart = prevend = i
                continue
            if i == prevend+1:
                prevend = i
                continue
            yield (prevstart, prevend-prevstart+1)
            prevstart = prevend = i
        if prevstart is not None:
            yield (prevstart, prevend-prevstart+1)

    def __nonzero__(self): # this gets us bool()
        return self.len()

    def len(self):
        return len(self._have)

    def __add__(self, other):
        s = self.__class__(self)
        for (start, length) in other:
            s.add(start, length)
        return s

    def __sub__(self, other):
        s = self.__class__(self)
        for (start, length) in other:
            s.remove(start, length)
        return s

    def __iadd__(self, other):
        for (start, length) in other:
            self.add(start, length)
        return self

    def __isub__(self, other):
        for (start, length) in other:
            self.remove(start, length)
        return self

    def __and__(self, other):
        s = self.__class__()
        for i in other.each():
            if i in self._have:
                s.add(i, 1)
        return s

    def __contains__(self, start_and_length):
        (start, length) = start_and_length
        for i in range(start, start+length):
            if i not in self._have:
                return False
        return True

class ByteSpans(unittest.TestCase):
    def test_basic(self):
        s = Spans()
        self.failUnlessEqual(list(s), [])
        self.failIf(s)
        self.failIf((0,1) in s)
        self.failUnlessEqual(s.len(), 0)

        s1 = Spans(3, 4) # 3,4,5,6
        self._check1(s1)

        s1 = Spans(long(3), long(4)) # 3,4,5,6
        self._check1(s1)

        s2 = Spans(s1)
        self._check1(s2)

        s2.add(10,2) # 10,11
        self._check1(s1)
        self.failUnless((10,1) in s2)
        self.failIf((10,1) in s1)
        self.failUnlessEqual(list(s2.each()), [3,4,5,6,10,11])
        self.failUnlessEqual(s2.len(), 6)

        s2.add(15,2).add(20,2)
        self.failUnlessEqual(list(s2.each()), [3,4,5,6,10,11,15,16,20,21])
        self.failUnlessEqual(s2.len(), 10)

        s2.remove(4,3).remove(15,1)
        self.failUnlessEqual(list(s2.each()), [3,10,11,16,20,21])
        self.failUnlessEqual(s2.len(), 6)

        s1 = SimpleSpans(3, 4) # 3 4 5 6
        s2 = SimpleSpans(5, 4) # 5 6 7 8
        i = s1 & s2
        self.failUnlessEqual(list(i.each()), [5, 6])

    def _check1(self, s):
        self.failUnlessEqual(list(s), [(3,4)])
        self.failUnless(s)
        self.failUnlessEqual(s.len(), 4)
        self.failIf((0,1) in s)
        self.failUnless((3,4) in s)
        self.failUnless((3,1) in s)
        self.failUnless((5,2) in s)
        self.failUnless((6,1) in s)
        self.failIf((6,2) in s)
        self.failIf((7,1) in s)
        self.failUnlessEqual(list(s.each()), [3,4,5,6])

    def test_large(self):
        s = Spans(4, 2**65) # don't do this with a SimpleSpans
        self.failUnlessEqual(list(s), [(4, 2**65)])
        self.failUnless(s)
        self.failUnlessEqual(s.len(), 2**65)
        self.failIf((0,1) in s)
        self.failUnless((4,2) in s)
        self.failUnless((2**65,2) in s)

    def test_math(self):
        s1 = Spans(0, 10) # 0,1,2,3,4,5,6,7,8,9
        s2 = Spans(5, 3) # 5,6,7
        s3 = Spans(8, 4) # 8,9,10,11

        s = s1 - s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,8,9])
        s = s1 - s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7])
        s = s2 - s3
        self.failUnlessEqual(list(s.each()), [5,6,7])
        s = s1 & s2
        self.failUnlessEqual(list(s.each()), [5,6,7])
        s = s2 & s1
        self.failUnlessEqual(list(s.each()), [5,6,7])
        s = s1 & s3
        self.failUnlessEqual(list(s.each()), [8,9])
        s = s3 & s1
        self.failUnlessEqual(list(s.each()), [8,9])
        s = s2 & s3
        self.failUnlessEqual(list(s.each()), [])
        s = s3 & s2
        self.failUnlessEqual(list(s.each()), [])
        s = Spans() & s3
        self.failUnlessEqual(list(s.each()), [])
        s = s3 & Spans()
        self.failUnlessEqual(list(s.each()), [])

        s = s1 + s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9])
        s = s1 + s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9,10,11])
        s = s2 + s3
        self.failUnlessEqual(list(s.each()), [5,6,7,8,9,10,11])

        s = Spans(s1)
        s -= s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,8,9])
        s = Spans(s1)
        s -= s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7])
        s = Spans(s2)
        s -= s3
        self.failUnlessEqual(list(s.each()), [5,6,7])

        s = Spans(s1)
        s += s2
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9])
        s = Spans(s1)
        s += s3
        self.failUnlessEqual(list(s.each()), [0,1,2,3,4,5,6,7,8,9,10,11])
        s = Spans(s2)
        s += s3
        self.failUnlessEqual(list(s.each()), [5,6,7,8,9,10,11])

    def test_random(self):
        # attempt to increase coverage of corner cases by comparing behavior
        # of a simple-but-slow model implementation against the
        # complex-but-fast actual implementation, in a large number of random
        # operations
        S1 = SimpleSpans
        S2 = Spans
        s1 = S1(); s2 = S2()
        seed = ""
        def _create(subseed):
            ns1 = S1(); ns2 = S2()
            for i in range(10):
                what = sha256(subseed+str(i))
                start = int(what[2:4], 16)
                length = max(1,int(what[5:6], 16))
                ns1.add(start, length); ns2.add(start, length)
            return ns1, ns2

        #print
        for i in range(1000):
            what = sha256(seed+str(i))
            op = what[0]
            subop = what[1]
            start = int(what[2:4], 16)
            length = max(1,int(what[5:6], 16))
            #print what
            if op in "0":
                if subop in "01234":
                    s1 = S1(); s2 = S2()
                elif subop in "5678":
                    s1 = S1(start, length); s2 = S2(start, length)
                else:
                    s1 = S1(s1); s2 = S2(s2)
                #print "s2 = %s" % s2.dump()
            elif op in "123":
                #print "s2.add(%d,%d)" % (start, length)
                s1.add(start, length); s2.add(start, length)
            elif op in "456":
                #print "s2.remove(%d,%d)" % (start, length)
                s1.remove(start, length); s2.remove(start, length)
            elif op in "78":
                ns1, ns2 = _create(what[7:11])
                #print "s2 + %s" % ns2.dump()
                s1 = s1 + ns1; s2 = s2 + ns2
            elif op in "9a":
                ns1, ns2 = _create(what[7:11])
                #print "%s - %s" % (s2.dump(), ns2.dump())
                s1 = s1 - ns1; s2 = s2 - ns2
            elif op in "bc":
                ns1, ns2 = _create(what[7:11])
                #print "s2 += %s" % ns2.dump()
                s1 += ns1; s2 += ns2
            elif op in "de":
                ns1, ns2 = _create(what[7:11])
                #print "%s -= %s" % (s2.dump(), ns2.dump())
                s1 -= ns1; s2 -= ns2
            else:
                ns1, ns2 = _create(what[7:11])
                #print "%s &= %s" % (s2.dump(), ns2.dump())
                s1 = s1 & ns1; s2 = s2 & ns2
            #print "s2 now %s" % s2.dump()
            self.failUnlessEqual(list(s1.each()), list(s2.each()))
            self.failUnlessEqual(s1.len(), s2.len())
            self.failUnlessEqual(bool(s1), bool(s2))
            self.failUnlessEqual(list(s1), list(s2))
            for j in range(10):
                what = sha256(what[12:14]+str(j))
                start = int(what[2:4], 16)
                length = max(1, int(what[5:6], 16))
                span = (start, length)
                self.failUnlessEqual(bool(span in s1), bool(span in s2))


    # s()
    # s(start,length)
    # s(s0)
    # s.add(start,length) : returns s
    # s.remove(start,length)
    # s.each() -> list of byte offsets, mostly for testing
    # list(s) -> list of (start,length) tuples, one per span
    # (start,length) in s -> True if (start..start+length-1) are all members
    #  NOT equivalent to x in list(s)
    # s.len() -> number of bytes, for testing, bool(), and accounting/limiting
    # bool(s)  (__nonzeron__)
    # s = s1+s2, s1-s2, +=s1, -=s1

    def test_overlap(self):
        for a in range(20):
            for b in range(10):
                for c in range(20):
                    for d in range(10):
                        self._test_overlap(a,b,c,d)

    def _test_overlap(self, a, b, c, d):
        s1 = set(range(a,a+b))
        s2 = set(range(c,c+d))
        #print "---"
        #self._show_overlap(s1, "1")
        #self._show_overlap(s2, "2")
        o = overlap(a,b,c,d)
        expected = s1.intersection(s2)
        if not expected:
            self.failUnlessEqual(o, None)
        else:
            start,length = o
            so = set(range(start,start+length))
            #self._show(so, "o")
            self.failUnlessEqual(so, expected)

    def _show_overlap(self, s, c):
        import sys
        out = sys.stdout
        if s:
            for i in range(max(s)):
                if i in s:
                    out.write(c)
                else:
                    out.write(" ")
        out.write("\n")

def extend(s, start, length, fill):
    if len(s) >= start+length:
        return s
    assert len(fill) == 1
    return s + fill*(start+length-len(s))

def replace(s, start, data):
    assert len(s) >= start+len(data)
    return s[:start] + data + s[start+len(data):]

class SimpleDataSpans(object):
    def __init__(self, other=None):
        self.missing = "" # "1" where missing, "0" where found
        self.data = ""
        if other:
            for (start, data) in other.get_chunks():
                self.add(start, data)

    def __nonzero__(self): # this gets us bool()
        return self.len()
    def len(self):
        return len(self.missing.replace("1", ""))
    def _dump(self):
        return [i for (i,c) in enumerate(self.missing) if c == "0"]
    def _have(self, start, length):
        m = self.missing[start:start+length]
        if not m or len(m)<length or int(m):
            return False
        return True
    def get_chunks(self):
        for i in self._dump():
            yield (i, self.data[i])
    def get_spans(self):
        return SimpleSpans([(start,len(data))
                            for (start,data) in self.get_chunks()])
    def get(self, start, length):
        if self._have(start, length):
            return self.data[start:start+length]
        return None
    def pop(self, start, length):
        data = self.get(start, length)
        if data:
            self.remove(start, length)
        return data
    def remove(self, start, length):
        self.missing = replace(extend(self.missing, start, length, "1"),
                               start, "1"*length)
    def add(self, start, data):
        self.missing = replace(extend(self.missing, start, len(data), "1"),
                               start, "0"*len(data))
        self.data = replace(extend(self.data, start, len(data), " "),
                            start, data)


class StringSpans(unittest.TestCase):
    def do_basic(self, klass):
        ds = klass()
        self.failUnlessEqual(ds.len(), 0)
        self.failUnlessEqual(list(ds._dump()), [])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 0)
        s1 = ds.get_spans()
        self.failUnlessEqual(ds.get(0, 4), None)
        self.failUnlessEqual(ds.pop(0, 4), None)
        ds.remove(0, 4)

        ds.add(2, "four")
        self.failUnlessEqual(ds.len(), 4)
        self.failUnlessEqual(list(ds._dump()), [2,3,4,5])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 4)
        s1 = ds.get_spans()
        self.failUnless((2,2) in s1)
        self.failUnlessEqual(ds.get(0, 4), None)
        self.failUnlessEqual(ds.pop(0, 4), None)
        self.failUnlessEqual(ds.get(4, 4), None)

        ds2 = klass(ds)
        self.failUnlessEqual(ds2.len(), 4)
        self.failUnlessEqual(list(ds2._dump()), [2,3,4,5])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds2.get_chunks()]), 4)
        self.failUnlessEqual(ds2.get(0, 4), None)
        self.failUnlessEqual(ds2.pop(0, 4), None)
        self.failUnlessEqual(ds2.pop(2, 3), "fou")
        self.failUnlessEqual(sum([len(d) for (s,d) in ds2.get_chunks()]), 1)
        self.failUnlessEqual(ds2.get(2, 3), None)
        self.failUnlessEqual(ds2.get(5, 1), "r")
        self.failUnlessEqual(ds.get(2, 3), "fou")
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 4)

        ds.add(0, "23")
        self.failUnlessEqual(ds.len(), 6)
        self.failUnlessEqual(list(ds._dump()), [0,1,2,3,4,5])
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 6)
        self.failUnlessEqual(ds.get(0, 4), "23fo")
        self.failUnlessEqual(ds.pop(0, 4), "23fo")
        self.failUnlessEqual(sum([len(d) for (s,d) in ds.get_chunks()]), 2)
        self.failUnlessEqual(ds.get(0, 4), None)
        self.failUnlessEqual(ds.pop(0, 4), None)

        ds = klass()
        ds.add(2, "four")
        ds.add(3, "ea")
        self.failUnlessEqual(ds.get(2, 4), "fear")

        ds = klass()
        ds.add(long(2), "four")
        ds.add(long(3), "ea")
        self.failUnlessEqual(ds.get(long(2), long(4)), "fear")


    def do_scan(self, klass):
        # do a test with gaps and spans of size 1 and 2
        #  left=(1,11) * right=(1,11) * gapsize=(1,2)
        # 111, 112, 121, 122, 211, 212, 221, 222
        #    211
        #      121
        #         112
        #            212
        #               222
        #                   221
        #                      111
        #                        122
        #  11 1  1 11 11  11  1 1  111
        # 0123456789012345678901234567
        # abcdefghijklmnopqrstuvwxyz-=
        pieces = [(1, "bc"),
                  (4, "e"),
                  (7, "h"),
                  (9, "jk"),
                  (12, "mn"),
                  (16, "qr"),
                  (20, "u"),
                  (22, "w"),
                  (25, "z-="),
                  ]
        p_elements = set([1,2,4,7,9,10,12,13,16,17,20,22,25,26,27])
        S = "abcdefghijklmnopqrstuvwxyz-="
        # TODO: when adding data, add capital letters, to make sure we aren't
        # just leaving the old data in place
        l = len(S)
        def base():
            ds = klass()
            for start, data in pieces:
                ds.add(start, data)
            return ds
        def dump(s):
            p = set(s._dump())
            d = "".join([((i not in p) and " " or S[i]) for i in range(l)])
            assert len(d) == l
            return d
        DEBUG = False
        for start in range(0, l):
            for end in range(start+1, l):
                # add [start-end) to the baseline
                which = "%d-%d" % (start, end-1)
                p_added = set(range(start, end))
                b = base()
                if DEBUG:
                    print()
                    print(dump(b), which)
                    add = klass(); add.add(start, S[start:end])
                    print(dump(add))
                b.add(start, S[start:end])
                if DEBUG:
                    print(dump(b))
                # check that the new span is there
                d = b.get(start, end-start)
                self.failUnlessEqual(d, S[start:end], which)
                # check that all the original pieces are still there
                for t_start, t_data in pieces:
                    t_len = len(t_data)
                    self.failUnlessEqual(b.get(t_start, t_len),
                                         S[t_start:t_start+t_len],
                                         "%s %d+%d" % (which, t_start, t_len))
                # check that a lot of subspans are mostly correct
                for t_start in range(l):
                    for t_len in range(1,4):
                        d = b.get(t_start, t_len)
                        if d is not None:
                            which2 = "%s+(%d-%d)" % (which, t_start,
                                                     t_start+t_len-1)
                            self.failUnlessEqual(d, S[t_start:t_start+t_len],
                                                 which2)
                        # check that removing a subspan gives the right value
                        b2 = klass(b)
                        b2.remove(t_start, t_len)
                        removed = set(range(t_start, t_start+t_len))
                        for i in range(l):
                            exp = (((i in p_elements) or (i in p_added))
                                   and (i not in removed))
                            which2 = "%s-(%d-%d)" % (which, t_start,
                                                     t_start+t_len-1)
                            self.failUnlessEqual(bool(b2.get(i, 1)), exp,
                                                 which2+" %d" % i)

    def test_test(self):
        self.do_basic(SimpleDataSpans)
        self.do_scan(SimpleDataSpans)

    def test_basic(self):
        self.do_basic(DataSpans)
        self.do_scan(DataSpans)

    def test_random(self):
        # attempt to increase coverage of corner cases by comparing behavior
        # of a simple-but-slow model implementation against the
        # complex-but-fast actual implementation, in a large number of random
        # operations
        S1 = SimpleDataSpans
        S2 = DataSpans
        s1 = S1(); s2 = S2()
        seed = ""
        def _randstr(length, seed):
            created = 0
            pieces = []
            while created < length:
                piece = sha256(seed + str(created))
                pieces.append(piece)
                created += len(piece)
            return "".join(pieces)[:length]
        def _create(subseed):
            ns1 = S1(); ns2 = S2()
            for i in range(10):
                what = sha256(subseed+str(i))
                start = int(what[2:4], 16)
                length = max(1,int(what[5:6], 16))
                ns1.add(start, _randstr(length, what[7:9]));
                ns2.add(start, _randstr(length, what[7:9]))
            return ns1, ns2

        #print
        for i in range(1000):
            what = sha256(seed+str(i))
            op = what[0]
            subop = what[1]
            start = int(what[2:4], 16)
            length = max(1,int(what[5:6], 16))
            #print what
            if op in "0":
                if subop in "0123456":
                    s1 = S1(); s2 = S2()
                else:
                    s1, s2 = _create(what[7:11])
                #print "s2 = %s" % list(s2._dump())
            elif op in "123456":
                #print "s2.add(%d,%d)" % (start, length)
                s1.add(start, _randstr(length, what[7:9]));
                s2.add(start, _randstr(length, what[7:9]))
            elif op in "789abc":
                #print "s2.remove(%d,%d)" % (start, length)
                s1.remove(start, length); s2.remove(start, length)
            else:
                #print "s2.pop(%d,%d)" % (start, length)
                d1 = s1.pop(start, length); d2 = s2.pop(start, length)
                self.failUnlessEqual(d1, d2)
            #print "s1 now %s" % list(s1._dump())
            #print "s2 now %s" % list(s2._dump())
            self.failUnlessEqual(s1.len(), s2.len())
            self.failUnlessEqual(list(s1._dump()), list(s2._dump()))
            for j in range(100):
                what = sha256(what[12:14]+str(j))
                start = int(what[2:4], 16)
                length = max(1, int(what[5:6], 16))
                d1 = s1.get(start, length); d2 = s2.get(start, length)
                self.failUnlessEqual(d1, d2, "%d+%d" % (start, length))

class YAML(unittest.TestCase):
    def test_convert(self):
        data = yaml.safe_dump(["str", u"unicode", u"\u1234nicode"])
        back = yamlutil.safe_load(data)
        self.failUnlessEqual(type(back[0]), unicode)
        self.failUnlessEqual(type(back[1]), unicode)
        self.failUnlessEqual(type(back[2]), unicode)
