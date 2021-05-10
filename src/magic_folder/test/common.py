from __future__ import print_function

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
    SOCK_STREAM,
    SOMAXCONN,
    socket,
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
    IReactorSocket,
)
from twisted.internet.endpoints import AdoptedStreamServerEndpoint

from allmydata import uri
from allmydata.interfaces import IImmutableFileNode,\
                                 NotEnoughSharesError

from allmydata.check_results import CheckResults, CheckAndRepairResults

from allmydata.storage_client import StubServer
from allmydata.util import log, iputil
from allmydata.util.assertutil import precondition
from allmydata.util.consumer import download_to_data

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


class SameProcessStreamEndpointAssigner(object):
    """
    A fixture which can assign streaming server endpoints for use *in this
    process only*.

    An effort is made to avoid address collisions for this port but the logic
    for doing so is platform-dependent (sorry, Windows).

    This is more reliable than trying to listen on a hard-coded non-zero port
    number.  It is at least as reliable as trying to listen on port number
    zero on Windows and more reliable than doing that on other platforms.
    """
    def setUp(self):
        self._cleanups = []
        # Make sure the `adopt-socket` endpoint is recognized.  We do this
        # instead of providing a dropin because we don't want to make this
        # endpoint available to random other applications.
        f = UseTestPlugins()
        f.setUp()
        self._cleanups.append(f.cleanUp)

    def tearDown(self):
        for c in self._cleanups:
            c()

    def assign(self, reactor):
        """
        Make a new streaming server endpoint and return its string description.

        This is intended to help write config files that will then be read and
        used in this process.

        :param reactor: The reactor which will be used to listen with the
            resulting endpoint.  If it provides ``IReactorSocket`` then
            resulting reliability will be extremely high.  If it doesn't,
            resulting reliability will be pretty alright.

        :return: A two-tuple of (location hint, port endpoint description) as
            strings.
        """
        if IReactorSocket.providedBy(reactor):
            # On this platform, we can reliable pre-allocate a listening port.
            # Once it is bound we know it will not fail later with EADDRINUSE.
            s = socket(AF_INET, SOCK_STREAM)
            # We need to keep ``s`` alive as long as the file descriptor we put in
            # this string might still be used.  We could dup() the descriptor
            # instead but then we've only inverted the cleanup problem: gone from
            # don't-close-too-soon to close-just-late-enough.  So we'll leave
            # ``s`` alive and use it as the cleanup mechanism.
            self._cleanups.append(s.close)
            s.setblocking(False)
            really_bind(s, ("127.0.0.1", 0))
            s.listen(SOMAXCONN)
            host, port = s.getsockname()
            location_hint = "tcp:%s:%d" % (host, port)
            port_endpoint = "adopt-socket:fd=%d" % (s.fileno(),)
        else:
            # On other platforms, we blindly guess and hope we get lucky.
            portnum = iputil.allocate_tcp_port()
            location_hint = "tcp:127.0.0.1:%d" % (portnum,)
            port_endpoint = "tcp:%d:interface=127.0.0.1" % (portnum,)

        return location_hint, port_endpoint

@implementer(IPullProducer)
class DummyProducer(object):
    def resumeProducing(self):
        pass

@implementer(IImmutableFileNode)
class FakeCHKFileNode(object):
    """I provide IImmutableFileNode, but all of my data is stored in a
    class-level dictionary."""

    def __init__(self, filecap, all_contents):
        precondition(isinstance(filecap, (uri.CHKFileURI, uri.LiteralFileURI)), filecap)
        self.all_contents = all_contents
        self.my_uri = filecap
        self.storage_index = self.my_uri.get_storage_index()

    def get_uri(self):
        return self.my_uri.to_string()
    def get_write_uri(self):
        return None
    def get_readonly_uri(self):
        return self.my_uri.to_string()
    def get_cap(self):
        return self.my_uri
    def get_verify_cap(self):
        return self.my_uri.get_verify_cap()
    def get_repair_cap(self):
        return self.my_uri.get_verify_cap()
    def get_storage_index(self):
        return self.storage_index

    def check(self, monitor, verify=False, add_lease=False):
        s = StubServer("\x00"*20)
        r = CheckResults(self.my_uri, self.storage_index,
                         healthy=True, recoverable=True,
                         count_happiness=10,
                         count_shares_needed=3,
                         count_shares_expected=10,
                         count_shares_good=10,
                         count_good_share_hosts=10,
                         count_recoverable_versions=1,
                         count_unrecoverable_versions=0,
                         servers_responding=[s],
                         sharemap={1: [s]},
                         count_wrong_shares=0,
                         list_corrupt_shares=[],
                         count_corrupt_shares=0,
                         list_incompatible_shares=[],
                         count_incompatible_shares=0,
                         summary="",
                         report=[],
                         share_problems=[],
                         servermap=None)
        return defer.succeed(r)
    def check_and_repair(self, monitor, verify=False, add_lease=False):
        d = self.check(verify)
        def _got(cr):
            r = CheckAndRepairResults(self.storage_index)
            r.pre_repair_results = r.post_repair_results = cr
            return r
        d.addCallback(_got)
        return d

    def is_mutable(self):
        return False
    def is_readonly(self):
        return True
    def is_unknown(self):
        return False
    def is_allowed_in_immutable_directory(self):
        return True
    def raise_error(self):
        pass

    def get_size(self):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            return self.my_uri.get_size()
        try:
            data = self.all_contents[self.my_uri.to_string()]
        except KeyError as le:
            raise NotEnoughSharesError(le, 0, 3)
        return len(data)
    def get_current_size(self):
        return defer.succeed(self.get_size())

    def read(self, consumer, offset=0, size=None):
        # we don't bother to call registerProducer/unregisterProducer,
        # because it's a hassle to write a dummy Producer that does the right
        # thing (we have to make sure that DummyProducer.resumeProducing
        # writes the data into the consumer immediately, otherwise it will
        # loop forever).

        d = defer.succeed(None)
        d.addCallback(self._read, consumer, offset, size)
        return d

    def _read(self, ignored, consumer, offset, size):
        if isinstance(self.my_uri, uri.LiteralFileURI):
            data = self.my_uri.data
        else:
            if self.my_uri.to_string() not in self.all_contents:
                raise NotEnoughSharesError(None, 0, 3)
            data = self.all_contents[self.my_uri.to_string()]
        start = offset
        if size is not None:
            end = offset + size
        else:
            end = len(data)
        consumer.write(data[start:end])
        return consumer


    def get_best_readable_version(self):
        return defer.succeed(self)


    def download_to_data(self, progress=None):
        return download_to_data(self, progress=progress)


    download_best_version = download_to_data


    def get_size_of_best_version(self):
        return defer.succeed(self.get_size)


def make_chk_file_cap(size):
    return uri.CHKFileURI(key=os.urandom(16),
                          uri_extension_hash=os.urandom(32),
                          needed_shares=3,
                          total_shares=10,
                          size=size)
def make_chk_file_uri(size):
    return make_chk_file_cap(size).to_string()

def create_chk_filenode(contents, all_contents):
    filecap = make_chk_file_cap(len(contents))
    n = FakeCHKFileNode(filecap, all_contents)
    all_contents[filecap.to_string()] = contents
    return n


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
