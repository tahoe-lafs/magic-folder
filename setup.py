#! /usr/bin/env python
# -*- coding: utf-8 -*-
import sys

# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2006-2012 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.

import os, subprocess, re

basedir = os.path.dirname(os.path.abspath(__file__))

# locate our version number

def read_version_py(infname):
    try:
        verstrline = open(infname, "rt").read()
    except EnvironmentError:
        return None
    else:
        VSRE = r"^verstr = ['\"]([^'\"]*)['\"]"
        mo = re.search(VSRE, verstrline, re.M)
        if mo:
            return mo.group(1)

VERSION_PY_FILENAME = 'src/magic_folder/_version.py'
version = read_version_py(VERSION_PY_FILENAME)

install_requires = [
    # we don't need much out of setuptools but the version checking stuff
    # needs pkg_resources and PEP 440 version specifiers.  We do need it to be
    # Python 2 compatible though, so don't venture to 45 and beyond.
    "setuptools >= 28.8.0, <45",

    "importlib_metadata",

    # zope.interface >= 3.6.0 is required for Twisted >= 12.1.0.
    # zope.interface 3.6.3 and 3.6.4 are incompatible with Nevow (#1435).
    "zope.interface >= 3.6.0, != 3.6.3, != 3.6.4",

    "PyYAML >= 3.11",

    "six >= 1.10.0",

    # Eliot is contemplating dropping Python 2 support.  Stick to a version we
    # know works on Python 2.7.
    "eliot ~= 1.7",

    # A great way to define types of values. (Same restrictions as tahoe 1.15.1)
    "attrs >= 18.2.0, < 20.0",

    # WebSocket library for twisted and asyncio
    "autobahn >= 19.5.2",

    "hyperlink",

    # Of course, we depend on Twisted.  Let Tahoe-LAFS' Twisted dependency
    # declaration serve, though.  Otherwise we have to be careful to agree on
    # which extras to pull in.
    #
    # Additionally, pin Tahoe-LAFS to a specific version we know works.
    # Magic-Folder uses a lot of private Tahoe-LAFS Python APIs so there's
    # good reason to expect things to break from release to release.  Pin a
    # specific version so we can upgrade intentionally when we know it will
    # work.
    "tahoe-lafs == 1.15.1",

    # twisted-based HTTP client
    "treq",

    # find the default location for configuration on different OSes
    "appdirs",

    # Python utilities that were originally extracted from tahoe
    # We use them directly, rather than the re-exports from allmydata
    "pyutil >= 3.3.0",

    # last py2 release of klein
    "klein==20.6.0",
]

setup_requires = [
    'setuptools >= 28.8.0, <45',  # for PEP-440 style versions
]

from setuptools import find_packages, setup
from setuptools import Command
from setuptools.command import install


trove_classifiers=[
    "Development Status :: 5 - Production/Stable",
    "Environment :: Console",
    "Environment :: Web Environment",
    "License :: OSI Approved :: GNU General Public License (GPL)",
    "License :: DFSG approved",
    "License :: Other/Proprietary License",
    "Intended Audience :: Developers",
    "Intended Audience :: End Users/Desktop",
    "Intended Audience :: System Administrators",
    "Operating System :: Microsoft",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: Unix",
    "Operating System :: POSIX :: Linux",
    "Operating System :: POSIX",
    "Operating System :: MacOS :: MacOS X",
    "Operating System :: OS Independent",
    "Natural Language :: English",
    "Programming Language :: Python",
    "Programming Language :: Python :: 2",
    "Programming Language :: Python :: 2.7",
    "Topic :: Utilities",
    "Topic :: System :: Systems Administration",
    "Topic :: System :: Filesystems",
    "Topic :: System :: Distributed Computing",
    "Topic :: Software Development :: Libraries",
    "Topic :: System :: Archiving :: Mirroring",
    "Topic :: System :: Archiving",
    ]


GIT_VERSION_BODY = '''
# This _version.py is generated from git metadata by the magic_folder setup.py.

__pkgname__ = "%(pkgname)s"
real_version = "%(version)s"
full_version = "%(full)s"
branch = "%(branch)s"
verstr = "%(normalized)s"
__version__ = verstr
'''

def run_command(args, cwd=None):
    use_shell = sys.platform == "win32"
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, cwd=cwd, shell=use_shell)
    except EnvironmentError as e:
        print("Warning: unable to run %r." % (" ".join(args),))
        print(e)
        return None
    stdout = p.communicate()[0].strip()
    if p.returncode != 0:
        print("Warning: %r returned error code %r." % (" ".join(args), p.returncode))
        return None
    return stdout


def versions_from_git(tag_prefix):
    # This runs 'git' from the directory that contains this file. That either
    # means someone ran a setup.py command (and this code is in
    # versioneer.py, thus the containing directory is the root of the source
    # tree), or someone ran a project-specific entry point (and this code is
    # in _version.py, thus the containing directory is somewhere deeper in
    # the source tree). This only gets called if the git-archive 'subst'
    # variables were *not* expanded, and _version.py hasn't already been
    # rewritten with a short version string, meaning we're inside a checked
    # out source tree.

    # versions_from_git (as copied from python-versioneer) returns strings
    # like "1.9.0-25-gb73aba9-dirty", which means we're in a tree with
    # uncommited changes (-dirty), the latest checkin is revision b73aba9,
    # the most recent tag was 1.9.0, and b73aba9 has 25 commits that weren't
    # in 1.9.0 . The narrow-minded NormalizedVersion parser that takes our
    # output (meant to enable sorting of version strings) refuses most of
    # that. magic_folder uses a function named suggest_normalized_version()
    # that can handle "1.9.0.post25", so dumb down our output to match.

    try:
        source_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError as e:
        # some py2exe/bbfreeze/non-CPython implementations don't do __file__
        print("Warning: unable to find version because we could not obtain the source directory.")
        print(e)
        return {}
    stdout = run_command(["git", "describe", "--tags", "--dirty", "--always"],
                         cwd=source_dir)
    if stdout is None:
        # run_command already complained.
        return {}
    stdout = stdout.decode("ascii")
    if not stdout.startswith(tag_prefix):
        print("Warning: tag %r doesn't start with prefix %r." % (stdout, tag_prefix))
        return {}
    version = stdout[len(tag_prefix):]
    pieces = version.split("-")
    if len(pieces) == 1:
        normalized_version = pieces[0]
    else:
        normalized_version = "%s.post%s" % (pieces[0], pieces[1])

    stdout = run_command(["git", "rev-parse", "HEAD"], cwd=source_dir)
    if stdout is None:
        # run_command already complained.
        return {}
    full = stdout.decode("ascii").strip()
    if version.endswith("-dirty"):
        full += "-dirty"
        normalized_version += ".dev0"

    # Thanks to Jistanidiot at <http://stackoverflow.com/questions/6245570/get-current-branch-name>.
    stdout = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=source_dir)
    branch = (stdout or b"unknown").decode("ascii").strip()

    # this returns native strings (bytes on py2, unicode on py3)
    return {"version": version, "normalized": normalized_version,
            "full": full, "branch": branch}

# setup.cfg has an [aliases] section which runs "update_version" before many
# commands (like "build" and "sdist") that need to know our package version
# ahead of time. If you add different commands (or if we forgot some), you
# may need to add it to setup.cfg and configure it to run update_version
# before your command.

class UpdateVersion(Command):
    description = "update _version.py from revision-control metadata"
    user_options = install.install.user_options

    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        global version
        verstr = version
        if os.path.isdir(os.path.join(basedir, ".git")):
            verstr = self.try_from_git()

        if verstr:
            self.distribution.metadata.version = verstr
        else:
            print("""\
********************************************************************
Warning: no version information found. This may cause tests to fail.
********************************************************************
""")

    def try_from_git(self):
        # If we change the release tag names, we must change this too
        versions = versions_from_git("magic-folder-")

        # setup.py might be run by either py2 or py3 (when run by tox, which
        # uses py3 on modern debian/ubuntu distros). We want this generated
        # file to contain native strings on both (str=bytes in py2,
        # str=unicode in py3)
        if versions:
            body = GIT_VERSION_BODY % {
                "pkgname": self.distribution.get_name(),
                "version": versions["version"],
                "normalized": versions["normalized"],
                "full": versions["full"],
                "branch": versions["branch"],
                }
            f = open(VERSION_PY_FILENAME, "wb")
            f.write(body.encode("ascii"))
            f.close()
            print("Wrote normalized version %r into '%s'" % (versions["normalized"], VERSION_PY_FILENAME))

        return versions.get("normalized", None)

class PleaseUseTox(Command):
    user_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass

    def run(self):
        print("ERROR: Please use 'tox' to run the test suite.")
        sys.exit(1)

setup_args = {}
if version:
    setup_args["version"] = version

setup(name="magic_folder",
      description='Tahoe-LAFS-based file synchronization',
      long_description=open('README.rst', 'rU').read(),
      author='the Tahoe-LAFS developers, the Magic-Folder developers',
      author_email='tahoe-dev@tahoe-lafs.org',
      url='https://github.com/LeastAuthority/magic_folder/',
      license='GNU GPL', # see README.rst -- there is an alternative licence
      cmdclass={"update_version": UpdateVersion,
                "test": PleaseUseTox,
                },
      package_dir={'':'src'},
      packages=find_packages('src') + ["twisted.plugins", "magic_folder.test.plugins"],
      classifiers=trove_classifiers,
      python_requires="~=2.7",
      install_requires=install_requires,
      extras_require={
          # For magic-folder on "darwin" (macOS) and the BSDs
          # Pin to < 0.10.4 to fix tests.
          # See https://github.com/LeastAuthority/magic-folder/issues/345
          ':sys_platform!="win32" and sys_platform!="linux2"': ["watchdog<0.10.4"],
          "test": [
              # Pin a specific pyflakes so we don't have different folks
              # disagreeing on what is or is not a lint issue.  We can bump
              # this version from time to time, but we will do it
              # intentionally.
              "pyflakes == 2.1.0",
              # coverage 5.0 breaks the integration tests in some opaque way.
              # This probably needs to be addressed in a more permanent way
              # eventually...
              "coverage ~= 4.5",
              "tox",
              "mock",
              "pytest",
              "pytest-twisted",
              "hypothesis >= 3.6.1",
              "towncrier",
              "testtools",
              "fixtures",
          ],
      },
      package_data={"magic_folder": ["ported-modules.txt"],
                    },
      include_package_data=True,
      setup_requires=setup_requires,
      entry_points = {
          "console_scripts": [
              "magic-folder = magic_folder.cli:_entry",
              "magic-folder-api = magic_folder.api_cli:_entry",
          ],
      },
      **setup_args
)
