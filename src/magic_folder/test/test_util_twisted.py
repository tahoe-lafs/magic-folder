import attr
from eliot import log_call, start_action
from eliot.testing import UnflushedTracebacks
from hypothesis import settings
from hypothesis.errors import StopTest
from hypothesis.stateful import (RuleBasedStateMachine, initialize, invariant,
                                 precondition, rule, run_state_machine_as_test)
from hypothesis.strategies import booleans, data
from testtools.matchers import HasLength, MatchesPredicate
from twisted.internet.defer import Deferred
from twisted.internet.task import Clock

from ..util.twisted import PeriodicService
from .common import SyncTestCase


class FailedCall(Exception):
    """
    Exception used to indicate a failed call to the given
    function in :py:`PeriodicService` state based tests.
    """


@attr.s(eq=False)
class PeriodicServiceRuleMachine(RuleBasedStateMachine):
    """
    State machine to exercise a :py:`PeriodicService`.

    :ivar SyncTestCase case: Test case the machine is running in.
    :ivar Clock clock: Source of time for the service.
    :ivar PeriodicService service: The service being tested.
    :ivar List[Deferred[None]] current_calls:
        List of outstanding deferreds from calls to the function.
    :ivar bool should_call_soon: Whether there has been an explicit
        request to call the function that has not yet happened.
    """

    case = attr.ib()
    clock = attr.ib(init=False, factory=Clock)
    service = attr.ib(init=False)
    current_calls = attr.ib(init=False, factory=list)
    should_call_soon = attr.ib(init=False, default=False)
    failed_calls = attr.ib(init=False, default=0)

    @log_call(include_result=False)
    def f(self):
        """
        The function called by the service.

        If there has been a request to call the function, we mark that request
        as complete.

        This either returns a deferred that is recorded in the list of
        current calls, or a deferred that has already fired.
        """
        self.should_call_soon = False
        d = Deferred()
        self.current_calls.append(d)
        # These self.data.draw calls can raise `StopTest`, which the service
        # will catch and log. We flush them in `teardown`.
        if self.data.draw(booleans(), "in f, should return fired deferred"):
            if self.data.draw(booleans(), "in f, should return failure"):
                self.fail()
            else:
                self.result()
        return d

    @service.default
    def _create_service(self):
        return PeriodicService(self.clock, 1, self.f)

    def __attrs_post_init__(self):
        super(PeriodicServiceRuleMachine, self).__init__()
        self._action = start_action(action_type="machine")
        # We use __enter__ and __exit__ explicitly here so that
        # since there is no scope to use `with` to delimit a
        # run of the state machine.
        self._action.__enter__()

    @initialize(data=data())
    def init_data(self, data):
        """
        Store the result of hypothesis' ``data`` strategy, so
        we can make choices in ``f``.
        """
        self.data = data

    @invariant()
    def one_call(self):
        """
        There is only zero or one calls to the function.
        """
        self.case.assertThat(
            self.current_calls,
            MatchesPredicate(
                lambda l: len(l) <= 1, "There is more than one concurrent call."
            ),
        )

    @precondition(lambda self: self.current_calls)
    @rule()
    @log_call
    def result(self):
        """
        If there is an running call to the function,
        finish the call.
        """
        self.current_calls.pop(0).callback(None)

    @precondition(lambda self: self.current_calls)
    @rule()
    @log_call
    def fail(self):
        """
        If there is an running call to the function,
        finish the call with an exception.
        """
        self.current_calls.pop(0).errback(FailedCall())
        self.failed_calls += 1

    @rule()
    @log_call
    def start(self):
        """
        Start the service.
        """
        self.service.startService()

    @rule()
    @log_call
    def stop(self):
        """
        Stop the service.
        """
        self.service.stopService()

    @rule()
    @log_call
    def call(self):
        """
        Request a call to the function.
        """
        self.should_call_soon = True
        self.service.call_soon()

    @precondition(lambda self: self.should_call_soon)
    @invariant()
    def no_delay(self):
        """
        If there is a request to call the function,
        we should never schedule a delay to call it.
        """
        assert len(self.clock.getDelayedCalls()) == 0

    @precondition(lambda self: self.clock.getDelayedCalls())
    @rule()
    @log_call
    def advance(self):
        """
        If there are any delayed calls, advance the clock.
        """
        self.clock.advance(1)

    def teardown(self):
        self._action.__exit__(None, None, None)
        # These can be caused by the self.data.draw calls in given function.
        # We can ignore them.
        self.case.eliot_logger.flush_tracebacks(StopTest)
        # Ensure that each failure of the given function was logged.
        self.case.assertThat(
            self.case.eliot_logger.flush_tracebacks(FailedCall),
            HasLength(self.failed_calls),
        )
        # If we have any other logged tracebacks, that is unexpected and
        # should cause this run to fail.
        unflushed_tracebacks = self.case.eliot_logger.flushTracebacks(BaseException)
        if unflushed_tracebacks:
            raise UnflushedTracebacks(unflushed_tracebacks)


class PeriodicSerivceTests(SyncTestCase):
    def test_state_space(self):
        """
        Explore the state space of L{PeriodicService}
        """
        # One example is not enough to exercise the state machine.
        if settings.default.max_examples == 1:
            # We are running with the magic-folder-fast profile
            # so use a relatively small number of examples.
            state_settings = settings(max_examples=100)
        else:
            # We are running with the magic-folder-ci profile
            # so we can use a large number of examples.
            state_settings = settings(max_examples=1000)
        run_state_machine_as_test(
            lambda: PeriodicServiceRuleMachine(self),
            settings=state_settings,
        )
