from __future__ import (
    absolute_import,
    division,
    print_function,
)


__all__ = [
    "SyncTestCase",
    "AsyncTestCase",
    "AsyncBrokenTestCase",

    "flush_logged_errors",
    "skip",
    "skipIf",
]

import os
import tempfile
from functools import partial
from unittest import case as _case
from socket import (
    AF_INET,
    error as socket_error,
)
from errno import (
    EADDRINUSE,
)

import treq

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
from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.interfaces import IPullProducer
from twisted.python import failure
from twisted.python.filepath import FilePath
from twisted.application import service
from twisted.web.error import Error as WebError
from twisted.internet.interfaces import (
    IStreamServerEndpointStringParser,
)
from twisted.internet.endpoints import AdoptedStreamServerEndpoint
from twisted.python import log

from allmydata import uri

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


def really_bind(s, addr):
    # Arbitrarily decide we'll try 100 times.  We don't want to try forever in
    # case this is a persistent problem.  Trying is cheap, though, so we may
    # as well try a lot.  Hopefully the OS isn't so bad at allocating a port
    # for us that it takes more than 2 iterations.
    for i in range(100):
        try:
            s.bind(addr)
        except socket_error as e:
            if e.errno == EADDRINUSE:
                continue
            raise
        else:
            return
    raise Exception("Many bind attempts failed with EADDRINUSE")


@implementer(IPullProducer)
class DummyProducer(object):
    def resumeProducing(self):
        pass


def make_chk_file_cap(size):
    return uri.CHKFileURI(key=os.urandom(16),
                          uri_extension_hash=os.urandom(32),
                          needed_shares=3,
                          total_shares=10,
                          size=size)
def make_chk_file_uri(size):
    return make_chk_file_cap(size).to_string()


def make_mutable_file_cap():
    return uri.WriteableSSKFileURI(writekey=os.urandom(16),
                                   fingerprint=os.urandom(32))

def make_mdmf_mutable_file_cap():
    return uri.WriteableMDMFFileURI(writekey=os.urandom(16),
                                    fingerprint=os.urandom(32))

def make_mutable_file_uri(mdmf=False):
    if mdmf:
        uri = make_mdmf_mutable_file_cap()
    else:
        uri = make_mutable_file_cap()

    return uri.to_string()

def make_verifier_uri():
    return uri.SSKVerifierURI(storage_index=os.urandom(16),
                              fingerprint=os.urandom(32)).to_string()

class LoggingServiceParent(service.MultiService):
    def log(self, *args, **kwargs):
        return log.msg(*args, **kwargs)


# The maximum data size for a literal cap, from the Tahoe-LAFS LIT URI docs.
LITERAL_LIMIT = 55

# Some data that won't result in a literal cap.
TEST_DATA = "\x02" * (LITERAL_LIMIT + 1)

class ShouldFailMixin(object):
    def shouldFail(self, expected_failure, which, substring,
                   callable, *args, **kwargs):
        """Assert that a function call raises some exception. This is a
        Deferred-friendly version of TestCase.assertRaises() .

        Suppose you want to verify the following function:

         def broken(a, b, c):
             if a < 0:
                 raise TypeError('a must not be negative')
             return defer.succeed(b+c)

        You can use:
            d = self.shouldFail(TypeError, 'test name',
                                'a must not be negative',
                                broken, -4, 5, c=12)
        in your test method. The 'test name' string will be included in the
        error message, if any, because Deferred chains frequently make it
        difficult to tell which assertion was tripped.

        The substring= argument, if not None, must appear in the 'repr'
        of the message wrapped by this Failure, or the test will fail.
        """

        assert substring is None or isinstance(substring, str)
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    message = repr(res.value.args[0])
                    self.failUnless(substring in message,
                                    "%s: substring '%s' not in '%s'"
                                    % (which, substring, message))
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d

class WebErrorMixin(object):
    def explain_web_error(self, f):
        # an error on the server side causes the client-side getPage() to
        # return a failure(t.web.error.Error), and its str() doesn't show the
        # response body, which is where the useful information lives. Attach
        # this method as an errback handler, and it will reveal the hidden
        # message.
        f.trap(WebError)
        print("Web Error:", f.value, ":", f.value.response)
        return f

    def _shouldHTTPError(self, res, which, validator):
        if isinstance(res, failure.Failure):
            res.trap(WebError)
            return validator(res)
        else:
            self.fail("%s was supposed to Error, not get '%s'" % (which, res))

    def shouldHTTPError(self, which,
                        code=None, substring=None, response_substring=None,
                        callable=None, *args, **kwargs):
        # returns a Deferred with the response body
        assert substring is None or isinstance(substring, str)
        assert callable
        def _validate(f):
            if code is not None:
                self.failUnlessEqual(f.value.status, str(code), which)
            if substring:
                code_string = str(f)
                self.failUnless(substring in code_string,
                                "%s: substring '%s' not in '%s'"
                                % (which, substring, code_string))
            response_body = f.value.response
            if response_substring:
                self.failUnless(response_substring in response_body,
                                "%s: response substring '%s' not in '%s'"
                                % (which, response_substring, response_body))
            return response_body
        d = defer.maybeDeferred(callable, *args, **kwargs)
        d.addBoth(self._shouldHTTPError, which, _validate)
        return d

    @inlineCallbacks
    def assertHTTPError(self, url, code, response_substring,
                        method="get", persistent=False,
                        **args):
        response = yield treq.request(method, url, persistent=persistent,
                                      **args)
        body = yield response.content()
        self.assertEquals(response.code, code)
        if response_substring is not None:
            self.assertIn(response_substring, body)
        returnValue(body)


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

        :return bytes: The newly created path
        """
        cwd = FilePath(u".")
        # self.id returns a native string so split it on a native "."
        tmp = cwd.descendant(self.id().split("."))
        tmp.makedirs(ignoreExistingDirectory=True)
        # Remove group and other write permission, in case it was somehow
        # granted, so that when we invent a temporary filename beneath this
        # directory we're not subject to a collision attack.
        tmp.chmod(0o755)
        return tmp.child(u"tmp").temporarySibling().asBytesMode().path

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
