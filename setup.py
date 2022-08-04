#! /usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import (
    absolute_import,
    division,
    print_function,
)

import os
from setuptools import find_packages, setup


def load_requirements(filename):
    basedir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(basedir, "requirements", filename), "r") as f:
        return [
            line.rstrip("\n")
            for line in f.readlines()
            if not line.startswith(("#", "-r")) and line.rstrip("\n")
        ]


install_requires = load_requirements("base.in")
test_requires = load_requirements("test.in")
build_requires = load_requirements("build.in")


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
    "Topic :: Utilities",
    "Topic :: System :: Systems Administration",
    "Topic :: System :: Filesystems",
    "Topic :: System :: Distributed Computing",
    "Topic :: Software Development :: Libraries",
    "Topic :: System :: Archiving :: Mirroring",
    "Topic :: System :: Archiving",
    ]


setup(
    name="magic-folder",
    # no version= because setuptools_scm
    description="Tahoe-LAFS-based file synchronization",
    long_description=open("README.rst", "r").read(),
    author="the Tahoe-LAFS developers, the Magic-Folder developers",
    author_email="tahoe-dev@tahoe-lafs.org",
    url="https://github.com/LeastAuthority/magic-folder/",
    license="GNU GPL", # see README.rst -- there is an alternative licence
    package_dir={"": "src"},
    packages=find_packages("src") + ["twisted.plugins", "magic_folder.test.plugins"],
    classifiers=trove_classifiers,
    install_requires=install_requires,
    extras_require={
        "test": test_requires,
        "build": build_requires,
    },
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "magic-folder = magic_folder.cli:_entry",
            "magic-folder-api = magic_folder.api_cli:_entry",
        ],
    },
)
