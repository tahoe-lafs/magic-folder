
import sys
import pkg_resources
from operator import (
    setitem,
)
from twisted.trial import unittest

from allmydata.version_checks import (
    _cross_check as cross_check,
    _extract_openssl_version as extract_openssl_version,
    _get_package_versions_and_locations as get_package_versions_and_locations,
)
from allmydata.util.verlib import NormalizedVersion as V, \
                                  IrrationalVersionError, \
                                  suggest_normalized_version as suggest


class MockSSL(object):
    SSLEAY_VERSION = 0
    SSLEAY_CFLAGS = 2

    def __init__(self, version, compiled_without_heartbeats=False):
        self.opts = {
            self.SSLEAY_VERSION: version,
            self.SSLEAY_CFLAGS: compiled_without_heartbeats and 'compiler: gcc -DOPENSSL_NO_HEARTBEATS'
                                                             or 'compiler: gcc',
        }

    def SSLeay_version(self, which):
        return self.opts[which]


class CheckRequirement(unittest.TestCase):
    def test_packages_from_pkg_resources(self):
        if hasattr(sys, 'frozen'):
            raise unittest.SkipTest("This test doesn't apply to frozen builds.")

        class MockPackage(object):
            def __init__(self, project_name, version, location):
                self.project_name = project_name
                self.version = version
                self.location = location

        def call_pkg_resources_require(*args):
            return [MockPackage("Foo", "1.0", "/path")]
        self.patch(pkg_resources, 'require', call_pkg_resources_require)

        (packages, errors) = get_package_versions_and_locations()
        self.failUnlessIn(("foo", ("1.0", "/path", "according to pkg_resources")), packages)
        self.failIfEqual(errors, [])
        self.failUnlessEqual([e for e in errors if "was not found by pkg_resources" not in e], [])

    def test_cross_check_unparseable_versions(self):
        # The bug in #1355 is triggered when a version string from either pkg_resources or import
        # is not parseable at all by normalized_version.

        res = cross_check({"foo": ("unparseable", "")}, [("foo", ("1.0", "", None))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("1.0", "")}, [("foo", ("unparseable", "", None))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("unparseable", "")}, [("foo", ("unparseable", "", None))])
        self.failUnlessEqual(res, [])

    def test_cross_check(self):
        res = cross_check({}, [])
        self.failUnlessEqual(res, [])

        res = cross_check({}, [("tahoe-lafs", ("1.0", "", "blah"))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("unparseable", "")}, [])
        self.failUnlessEqual(res, [])

        res = cross_check({"argparse": ("unparseable", "")}, [])
        self.failUnlessEqual(res, [])

        res = cross_check({}, [("foo", ("unparseable", "", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("version 'unparseable'", res[0])
        self.failUnlessIn("was not found by pkg_resources", res[0])

        res = cross_check({"distribute": ("1.0", "/somewhere")}, [("setuptools", ("2.0", "/somewhere", "distribute"))])
        self.failUnlessEqual(res, [])

        res = cross_check({"distribute": ("1.0", "/somewhere")}, [("setuptools", ("2.0", "/somewhere", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("location mismatch", res[0])

        res = cross_check({"distribute": ("1.0", "/somewhere")}, [("setuptools", ("2.0", "/somewhere_different", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("location mismatch", res[0])

        res = cross_check({"zope.interface": ("1.0", "")}, [("zope.interface", ("unknown", "", None))])
        self.failUnlessEqual(res, [])

        res = cross_check({"zope.interface": ("unknown", "")}, [("zope.interface", ("unknown", "", None))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("1.0", "")}, [("foo", ("unknown", "", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("could not find a version number", res[0])

        res = cross_check({"foo": ("unknown", "")}, [("foo", ("unknown", "", None))])
        self.failUnlessEqual(res, [])

        # When pkg_resources and import both find a package, there is only a warning if both
        # the version and the path fail to match.

        res = cross_check({"foo": ("1.0", "/somewhere")}, [("foo", ("2.0", "/somewhere", None))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("1.0", "/somewhere")}, [("foo", ("1.0", "/somewhere_different", None))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("1.0-r123", "/somewhere")}, [("foo", ("1.0.post123", "/somewhere_different", None))])
        self.failUnlessEqual(res, [])

        res = cross_check({"foo": ("1.0", "/somewhere")}, [("foo", ("2.0", "/somewhere_different", None))])
        self.failUnlessEqual(len(res), 1)
        self.failUnlessIn("but version '2.0'", res[0])

    def test_extract_openssl_version(self):
        self.failUnlessEqual(extract_openssl_version(MockSSL("")),
                                                     ("", None, None))
        self.failUnlessEqual(extract_openssl_version(MockSSL("NotOpenSSL a.b.c foo")),
                                                     ("NotOpenSSL", None, "a.b.c foo"))
        self.failUnlessEqual(extract_openssl_version(MockSSL("OpenSSL a.b.c")),
                                                     ("a.b.c", None, None))
        self.failUnlessEqual(extract_openssl_version(MockSSL("OpenSSL 1.0.1e 11 Feb 2013")),
                                                     ("1.0.1e", None, "11 Feb 2013"))
        self.failUnlessEqual(extract_openssl_version(MockSSL("OpenSSL 1.0.1e 11 Feb 2013", compiled_without_heartbeats=True)),
                                                     ("1.0.1e", None, "11 Feb 2013, no heartbeats"))


# based on https://bitbucket.org/tarek/distutilsversion/src/17df9a7d96ef/test_verlib.py

class VersionTestCase(unittest.TestCase):
    versions = ((V('1.0'), '1.0'),
                (V('1.1'), '1.1'),
                (V('1.2.3'), '1.2.3'),
                (V('1.2'), '1.2'),
                (V('1.2.3a4'), '1.2.3a4'),
                (V('1.2c4'), '1.2c4'),
                (V('1.2.3.4'), '1.2.3.4'),
                (V('1.2.3.4.0b3'), '1.2.3.4b3'),
                (V('1.2.0.0.0'), '1.2'),
                (V('1.0.dev345'), '1.0.dev345'),
                (V('1.0.post456.dev623'), '1.0.post456.dev623'))

    def test_basic_versions(self):
        for v, s in self.versions:
            self.failUnlessEqual(str(v), s)

    def test_from_parts(self):
        for v, s in self.versions:
            parts = v.parts
            v2 = V.from_parts(*parts)
            self.failUnlessEqual(v, v2)
            self.failUnlessEqual(str(v), str(v2))

    def test_irrational_versions(self):
        irrational = ('1', '1.2a', '1.2.3b', '1.02', '1.2a03',
                      '1.2a3.04', '1.2.dev.2', '1.2dev', '1.2.dev',
                      '1.2.dev2.post2', '1.2.post2.dev3.post4')

        for s in irrational:
            self.failUnlessRaises(IrrationalVersionError, V, s)

    def test_comparison(self):
        self.failUnlessRaises(TypeError, lambda: V('1.2.0') == '1.2')

        self.failUnlessEqual(V('1.2.0'), V('1.2'))
        self.failIfEqual(V('1.2.0'), V('1.2.3'))
        self.failUnless(V('1.2.0') < V('1.2.3'))
        self.failUnless(V('1.0') > V('1.0b2'))
        self.failUnless(V('1.0') > V('1.0c2') > V('1.0c1') > V('1.0b2') > V('1.0b1')
                        > V('1.0a2') > V('1.0a1'))
        self.failUnless(V('1.0.0') > V('1.0.0c2') > V('1.0.0c1') > V('1.0.0b2') > V('1.0.0b1')
                        > V('1.0.0a2') > V('1.0.0a1'))

        self.failUnless(V('1.0') < V('1.0.post456.dev623'))
        self.failUnless(V('1.0.post456.dev623') < V('1.0.post456')  < V('1.0.post1234'))

        self.failUnless(V('1.0a1')
                        < V('1.0a2.dev456')
                        < V('1.0a2')
                        < V('1.0a2.1.dev456')  # e.g. need to do a quick post release on 1.0a2
                        < V('1.0a2.1')
                        < V('1.0b1.dev456')
                        < V('1.0b2')
                        < V('1.0c1')
                        < V('1.0c2.dev456')
                        < V('1.0c2')
                        < V('1.0.dev7')
                        < V('1.0.dev18')
                        < V('1.0.dev456')
                        < V('1.0.dev1234')
                        < V('1.0')
                        < V('1.0.post456.dev623')  # development version of a post release
                        < V('1.0.post456'))

    def test_suggest_normalized_version(self):
        self.failUnlessEqual(suggest('1.0'), '1.0')
        self.failUnlessEqual(suggest('1.0-alpha1'), '1.0a1')
        self.failUnlessEqual(suggest('1.0c2'), '1.0c2')
        self.failUnlessEqual(suggest('walla walla washington'), None)
        self.failUnlessEqual(suggest('2.4c1'), '2.4c1')

        # from setuptools
        self.failUnlessEqual(suggest('0.4a1.r10'), '0.4a1.post10')
        self.failUnlessEqual(suggest('0.7a1dev-r66608'), '0.7a1.dev66608')
        self.failUnlessEqual(suggest('0.6a9.dev-r41475'), '0.6a9.dev41475')
        self.failUnlessEqual(suggest('2.4preview1'), '2.4c1')
        self.failUnlessEqual(suggest('2.4pre1') , '2.4c1')
        self.failUnlessEqual(suggest('2.1-rc2'), '2.1c2')

        # from pypi
        self.failUnlessEqual(suggest('0.1dev'), '0.1.dev0')
        self.failUnlessEqual(suggest('0.1.dev'), '0.1.dev0')

        # we want to be able to parse Twisted
        # development versions are like post releases in Twisted
        self.failUnlessEqual(suggest('9.0.0+r2363'), '9.0.0.post2363')

        # pre-releases are using markers like "pre1"
        self.failUnlessEqual(suggest('9.0.0pre1'), '9.0.0c1')

        # we want to be able to parse Tcl-TK
        # they use "p1" "p2" for post releases
        self.failUnlessEqual(suggest('1.4p1'), '1.4.post1')

        # from darcsver
        self.failUnlessEqual(suggest('1.8.1-r4956'), '1.8.1.post4956')

        # zetuptoolz
        self.failUnlessEqual(suggest('0.6c16dev3'), '0.6c16.dev3')


class T(unittest.TestCase):
    def test_report_import_error(self):
        """
        get_package_versions_and_locations reports a dependency if a dependency
        cannot be imported.
        """
        # Make sure we don't leave the system in a bad state.
        self.addCleanup(
            lambda foolscap=sys.modules["foolscap"]: setitem(
                sys.modules,
                "foolscap",
                foolscap,
            ),
        )
        # Make it look like Foolscap isn't installed.
        sys.modules["foolscap"] = None
        vers_and_locs, errors = get_package_versions_and_locations()

        foolscap_stuffs = [stuff for (pkg, stuff) in vers_and_locs if pkg == 'foolscap']
        self.failUnlessEqual(len(foolscap_stuffs), 1)
        self.failUnless([e for e in errors if "dependency \'foolscap\' could not be imported" in e])
