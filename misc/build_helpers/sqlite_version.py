# Copyright (C) Least Authority TFA GmbH
from __future__ import absolute_import, division, print_function

"""
dump the underlying sqlite3 version
"""

import sqlite3

print("sqlite3 version: {}".format(sqlite3.sqlite_version))
