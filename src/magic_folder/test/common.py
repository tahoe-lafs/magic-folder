__all__ = [
    "SyncTestCase",
    "AsyncTestCase",
    "AsyncBrokenTestCase",

    "flush_logged_errors",
    "skip",
    "skipIf",
    "success_result_of",
]

import os
import tempfile
from functools import partial
from unittest import case as _case
from socket import (
    AF_INET,
)

from zope.interface import implementer

from testtools import (
    TestCase,
    skip,
    skipIf,
)
from testtools.twistedsupport import (
    SynchronousDeferredRunTest,
    AsynchronousDeferredRunTest,
    AsynchronousDeferredRunTestForBrokenTwisted,
    flush_logged_errors,
)

from twisted.plugin import IPlugin
from twisted.python.filepath import FilePath
from twisted.internet.interfaces import (
    IStreamServerEndpointStringParser,
)
from twisted.internet.endpoints import AdoptedStreamServerEndpoint
from twisted.python import log
from twisted.trial.unittest import SynchronousTestCase as _TwistedSynchronousTestCase

from eliot import (
    log_call,
)

from .eliotutil import (
    EliotLoggedRunTest,
)


class UseTestPlugins(object):
    """
    A fixture which enables loading Twisted plugins from the Tahoe-LAFS test
    suite.
    """
    @log_call
    def setUp(self):
        """
        Add the testing package ``plugins`` directory to the ``twisted.plugins``
        aggregate package.
        """
        import twisted.plugins
        testplugins = FilePath(__file__).sibling("plugins")
        twisted.plugins.__path__.insert(0, testplugins.path)

    @log_call
    def cleanUp(self):
        """
        Remove the testing package ``plugins`` directory from the
        ``twisted.plugins`` aggregate package.
        """
        import twisted.plugins
        testplugins = FilePath(__file__).sibling("plugins")
        twisted.plugins.__path__.remove(testplugins.path)

    def getDetails(self):
        return {}


@implementer(IPlugin, IStreamServerEndpointStringParser)
class AdoptedServerPort(object):
    """
    Parse an ``adopt-socket:<fd>`` endpoint description by adopting ``fd`` as
    a listening TCP port.
    """
    prefix = "adopt-socket"

    def parseStreamServer(self, reactor, fd):
        log.msg("Adopting {}".format(fd))
        # AdoptedStreamServerEndpoint wants to own the file descriptor.  It
        # will duplicate it and then close the one we pass in.  This means it
        # is really only possible to adopt a particular file descriptor once.
        #
        # This wouldn't matter except one of the tests wants to stop one of
        # the nodes and start it up again.  This results in exactly an attempt
        # to adopt a particular file descriptor twice.
        #
        # So we'll dup it ourselves.  AdoptedStreamServerEndpoint can do
        # whatever it wants to the result - the original will still be valid
        # and reusable.
        return AdoptedStreamServerEndpoint(reactor, os.dup(int(fd)), AF_INET)


# The maximum data size for a literal cap, from the Tahoe-LAFS LIT URI docs.
LITERAL_LIMIT = 55

# Some data that won't result in a literal cap.
TEST_DATA = "\x02" * (LITERAL_LIMIT + 1)


class _TestCaseMixin(object):
    """
    A mixin for ``TestCase`` which collects helpful behaviors for subclasses.

    Those behaviors are:

    * All of the features of testtools TestCase.
    * Each test method will be run in a unique Eliot action context which
      identifies the test and collects all Eliot log messages emitted by that
      test (including setUp and tearDown messages).
    * trial-compatible mktemp method
    * unittest2-compatible assertRaises helper
    * Automatic cleanup of tempfile.tempdir mutation (pervasive through the
      Tahoe-LAFS test suite).
    """
    def setUp(self):
        # Restore the original temporary directory.  Node ``init_tempdir``
        # mangles it and many tests manage to get that method called.
        self.addCleanup(
            partial(setattr, tempfile, "tempdir", tempfile.tempdir),
        )
        return super(_TestCaseMixin, self).setUp()

    class _DummyCase(_case.TestCase):
        def dummy(self):
            pass
    _dummyCase = _DummyCase("dummy")

    def mktemp(self):
        """
        Create a new path name which can be used for a new file or directory.

        The result is a path that is guaranteed to be unique within the
        current working directory.  The parent of the path will exist, but the
        path will not.

        :return str: The newly created path
        """
        cwd = FilePath(u".")
        # self.id returns a native string so split it on a native "."
        tmp = cwd.descendant(self.id().split("."))
        tmp.makedirs(ignoreExistingDirectory=True)
        # Remove group and other write permission, in case it was somehow
        # granted, so that when we invent a temporary filename beneath this
        # directory we're not subject to a collision attack.
        tmp.chmod(0o755)
        return tmp.child(u"tmp").temporarySibling().asTextMode().path

    def assertRaises(self, *a, **kw):
        return self._dummyCase.assertRaises(*a, **kw)


class SyncTestCase(_TestCaseMixin, TestCase):
    """
    A ``TestCase`` which can run tests that may return an already-fired
    ``Deferred``.
    """
    run_tests_with = EliotLoggedRunTest.make_factory(
        SynchronousDeferredRunTest,
    )

    # without this method, instantiating a SyncTestCase (or
    # e.g. testtools.TestCase) results in a traceback (see
    # also test_common.py)
    def runTest(self, *a, **kw):
        raise NotImplementedError


class AsyncTestCase(_TestCaseMixin, TestCase):
    """
    A ``TestCase`` which can run tests that may return a Deferred that will
    only fire if the global reactor is running.
    """
    run_tests_with = EliotLoggedRunTest.make_factory(
        AsynchronousDeferredRunTest.make_factory(timeout=60.0),
    )


class AsyncBrokenTestCase(_TestCaseMixin, TestCase):
    """
    A ``TestCase`` like ``AsyncTestCase`` but which spins the reactor a little
    longer than apparently necessary to clean out lingering unaccounted for
    event sources.

    Tests which require this behavior are broken and should be fixed so they
    pass with ``AsyncTestCase``.
    """
    run_tests_with = EliotLoggedRunTest.make_factory(
        AsynchronousDeferredRunTestForBrokenTwisted.make_factory(timeout=60.0),
    )


# Twisted provides the useful function `successResultOf`, for getting
# the result of an already fired deferred. Unfortunately, it is only
# available as a method on trial's TestCase. Since we don't use that,
# we expose it as a free function here. While it only makes sense to
# use it in test code, that includes test fixtures, which may not have
# access to any test case.
_TWISTED_TEST_CASE = _TwistedSynchronousTestCase()
success_result_of = _TWISTED_TEST_CASE.successResultOf
