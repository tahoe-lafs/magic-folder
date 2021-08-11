#! /usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

import os
import sys

from setuptools import Command, find_packages, setup

# Tahoe-LAFS -- secure, distributed storage grid
#
# Copyright © 2006-2012 The Tahoe-LAFS Software Foundation
#
# This file is part of Tahoe-LAFS.
#
# See the docs/about.rst file for licensing information.


basedir = os.path.dirname(os.path.abspath(__file__))


def load_requirements(filename):
    with open(os.path.join(basedir, "requirements", filename), "r") as f:
        return [
            line.rstrip("\n")
            for line in f.readlines()
            if not line.startswith(("#", "-r")) and line.rstrip("\n")
        ]


install_requires = load_requirements("base.in")
test_requires = load_requirements("test.in")

setup_requires = [
    "setuptools >= 28.8.0, <45",  # for PEP-440 style versions
]


trove_classifiers = [
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


class PleaseUseTox(Command):
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        print("ERROR: Please use 'tox' to run the test suite.")
        sys.exit(1)


setup(
    name="magic_folder",
    version="0.1.0",
    description="Tahoe-LAFS-based file synchronization",
    long_description=open("README.rst", "rU").read(),
    author="the Tahoe-LAFS developers, the Magic-Folder developers",
    author_email="tahoe-dev@tahoe-lafs.org",
    url="https://github.com/LeastAuthority/magic_folder/",
    license="GNU GPL",  # see README.rst -- there is an alternative licence
    cmdclass={
        "test": PleaseUseTox,
    },
    package_dir={"": "src"},
    packages=find_packages("src") + ["twisted.plugins", "magic_folder.test.plugins"],
    classifiers=trove_classifiers,
    python_requires="~=2.7",
    install_requires=install_requires,
    extras_require={
        "test": test_requires,
    },
    package_data={
        "magic_folder": ["ported-modules.txt"],
    },
    include_package_data=True,
    setup_requires=setup_requires,
    entry_points={
        "console_scripts": [
            "magic-folder = magic_folder.cli:_entry",
            "magic-folder-api = magic_folder.api_cli:_entry",
        ],
    },
)
