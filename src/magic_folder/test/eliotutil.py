"""
Tools aimed at the interaction between tests and Eliot.
"""

__all__ = [
    "RUN_TEST",
    "EliotLoggedRunTest",
    "eliot_logged_test",
]

from functools import (
    wraps,
    partial,
)

import attr

from eliot import (
    ActionType,
    Field,
)
from eliot.testing import capture_logging
from eliot.twisted import DeferredContext

from twisted.internet.defer import (
    maybeDeferred,
)

_NAME = Field.for_types(
    u"name",
    [str],
    u"The name of the test.",
)

RUN_TEST = ActionType(
    u"run-test",
    [_NAME],
    [],
    u"A test is run.",
)


def eliot_logged_test(f):
    """
    Decorate a test method to run in a dedicated Eliot action context.

    The action will finish after the test is done (after the returned Deferred
    fires, if a Deferred is returned).  It will note the name of the test
    being run.

    All messages emitted by the test will be validated.  They will still be
    delivered to the global logger.
    """
    # A convenient, mutable container into which nested functions can write
    # state to be shared among them.
    class storage(object):
        pass

    @wraps(f)
    def run_and_republish(self, *a, **kw):
        # Unfortunately the only way to get at the global/default logger...
        # This import is delayed here so that we get the *current* default
        # logger at the time the decorated function is run.
        from eliot._output import _DEFAULT_LOGGER as default_logger

        def republish():
            # The memory logger captures both the raw messages and the
            # serializer. We pass them both to the global logger, so that the
            # expected serialization is preserved.
            logger = storage.logger
            for msg, serializer in zip(logger.messages, logger.serializers):
                # We copy the message here, as the cleanup function that
                # capture_logging installs will also try to serialize all
                # messages.
                default_logger.write(msg, serializer)

        @capture_logging(None)
        def run(self, logger):
            # Record the MemoryLogger for later message extraction.
            storage.logger = logger
            # Give the test access to the logger as well.  It would be just
            # fine to pass this as a keyword argument to `f` but implementing
            # that now will give me conflict headaches so I'm not doing it.
            self.eliot_logger = logger
            # Arrange for all messages written to the memory logger that
            # `capture_logging` installs to be re-written to the global/default
            # logger so they might end up in a log file somewhere, if someone
            # wants.  This has to be done *before* the cleanup function capture_logging
            # runs, as that function can serialize all, some or none of the captured
            # messages.
            self.addCleanup(republish)
            return f(self, *a, **kw)


        # Begin an action that should comprise all messages from the decorated
        # test method.
        with RUN_TEST(name=self.id()).context() as action:
            # When the test method Deferred fires, the RUN_TEST action is
            # done.  However, we won't have re-published the MemoryLogger
            # messages into the global/default logger when this Deferred
            # fires.  So we need to delay finishing the action until that has
            # happened.  Record the action so we can do that.
            storage.action = action

            # Support both Deferred-returning and non-Deferred-returning
            # tests.
            return DeferredContext(maybeDeferred(run, self)).addActionFinish()

    return run_and_republish


@attr.s
class EliotLoggedRunTest(object):
    """
    A *RunTest* implementation which surrounds test invocation with an
    Eliot-based action.

    This *RunTest* composes with another for convenience.

    :ivar case: The test case to run.

    :ivar handlers: Pass-through for the wrapped *RunTest*.
    :ivar last_resort: Pass-through for the wrapped *RunTest*.

    :ivar _run_tests_with_factory: A factory for the other *RunTest*.
    """
    _run_tests_with_factory = attr.ib()
    case = attr.ib()
    handlers = attr.ib(default=None)
    last_resort = attr.ib(default=None)

    @classmethod
    def make_factory(cls, delegated_run_test_factory):
        return partial(cls, delegated_run_test_factory)

    @property
    def eliot_logger(self):
        return self.case.eliot_logger

    @eliot_logger.setter
    def eliot_logger(self, value):
        self.case.eliot_logger = value

    def addCleanup(self, *a, **kw):
        return self.case.addCleanup(*a, **kw)

    def id(self):
        return self.case.id()

    @eliot_logged_test
    def run(self, result=None):
        return self._run_tests_with_factory(
            self.case,
            self.handlers,
            self.last_resort,
        ).run(result)
