#! /usr/bin/env python

from __future__ import print_function

import locale, os, platform, subprocess, sys, traceback


def foldlines(s, numlines=None):
    lines = s.split("\n")
    if numlines is not None:
        lines = lines[:numlines]
    return " ".join(lines).replace("\r", "")


def print_platform():
    try:
        import platform
        out = platform.platform()
        print("platform:", foldlines(out))
        print("machine: ", platform.machine())
        if hasattr(platform, 'linux_distribution'):
            print("linux_distribution:", repr(platform.linux_distribution()))
    except EnvironmentError:
        sys.stderr.write("\nGot exception using 'platform'. Exception follows\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()


def print_python_ver():
    print("python:", foldlines(sys.version))
    print('maxunicode: ' + str(sys.maxunicode))


def print_python_encoding_settings():
    print('filesystem.encoding: ' + str(sys.getfilesystemencoding()))
    print('locale.getpreferredencoding: ' + str(locale.getpreferredencoding()))
    try:
        print('locale.defaultlocale: ' + str(locale.getdefaultlocale()))
    except ValueError as e:
        print('got exception from locale.getdefaultlocale(): ', e)
    print('locale.locale: ' + str(locale.getlocale()))

def print_stdout(cmdlist, label=None, numlines=None):
    try:
        if label is None:
            label = cmdlist[0]
        res = subprocess.Popen(cmdlist, stdin=open(os.devnull),
                               stdout=subprocess.PIPE).communicate()[0]
        print(label + ': ' + foldlines(res.decode('utf-8'), numlines))
    except EnvironmentError as e:
        if isinstance(e, OSError) and e.errno == 2:
            print(label + ': no such file or directory')
            return
        sys.stderr.write("\nGot exception invoking '%s'. Exception follows.\n" % (cmdlist[0],))
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()


def print_as_ver():
    if os.path.exists('a.out'):
        print("WARNING: a file named a.out exists, and getting the version of the 'as' assembler "
              "writes to that filename, so I'm not attempting to get the version of 'as'.")
        return
    try:
        stdout, stderr = subprocess.Popen(['as', '-version'], stdin=open(os.devnull),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        print('as: ' + foldlines(stdout.decode('utf-8') + ' ' + stderr.decode('utf-8')))
        if os.path.exists('a.out'):
            os.remove('a.out')
    except EnvironmentError:
        sys.stderr.write("\nGot exception invoking '%s'. Exception follows.\n" % ('as',))
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()


def print_setuptools_ver():
    try:
        import pkg_resources
        out = str(pkg_resources.require("setuptools"))
        print("setuptools:", foldlines(out))
    except (ImportError, EnvironmentError):
        sys.stderr.write("\nGot exception using 'pkg_resources' to get the version of setuptools. Exception follows\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
    except pkg_resources.DistributionNotFound:
        print('setuptools: DistributionNotFound')


def print_py_pkg_ver(pkgname, modulename=None):
    if modulename is None:
        modulename = pkgname
    print()
    try:
        import pkg_resources
        out = str(pkg_resources.require(pkgname))
        print(pkgname + ': ' + foldlines(out))
    except (ImportError, EnvironmentError):
        sys.stderr.write("\nGot exception using 'pkg_resources' to get the version of %s. Exception follows.\n" % (pkgname,))
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
    except pkg_resources.DistributionNotFound:
        print(pkgname + ': DistributionNotFound')
    try:
        __import__(modulename)
    except ImportError:
        pass
    else:
        modobj = sys.modules.get(modulename)
        print(pkgname + ' module: ' + str(modobj))
        try:
            print(pkgname + ' __version__: ' + str(modobj.__version__))
        except AttributeError:
            pass


print_platform()
print()
print_python_ver()
print_stdout(['virtualenv', '--version'])
print_stdout(['tox', '--version'])
print()
print_stdout(['locale'])
print_python_encoding_settings()
print()
print_stdout(['buildbot', '--version'])
print_stdout(['buildslave', '--version'])
if 'windows' in platform.system().lower():
    print_stdout(['cl'])
print_stdout(['gcc', '--version'], numlines=1)
print_stdout(['g++', '--version'], numlines=1)
print_stdout(['cryptest', 'V'])
print_stdout(['git', '--version'])
print_stdout(['openssl', 'version'])
print_stdout(['flappclient', '--version'])
print_stdout(['valgrind', '--version'])

print_as_ver()

print_setuptools_ver()

print_py_pkg_ver('cffi')
print_py_pkg_ver('coverage')
print_py_pkg_ver('cryptography')
print_py_pkg_ver('foolscap')
print_py_pkg_ver('mock')
print_py_pkg_ver('Nevow', 'nevow')
print_py_pkg_ver('pyasn1')
print_py_pkg_ver('pycparser')
print_py_pkg_ver('cryptography')
print_py_pkg_ver('pyflakes')
print_py_pkg_ver('pyOpenSSL', 'OpenSSL')
print_py_pkg_ver('six')
print_py_pkg_ver('trialcoverage')
print_py_pkg_ver('Twisted', 'twisted')
print_py_pkg_ver('TwistedCore', 'twisted.python')
print_py_pkg_ver('TwistedWeb', 'twisted.web')
print_py_pkg_ver('TwistedConch', 'twisted.conch')
print_py_pkg_ver('zfec')
print_py_pkg_ver('zope.interface')
