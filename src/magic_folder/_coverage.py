import os

from twisted.application.service import (
    Service,
)

class _CoverageService(Service):
    def startService(self):
        import coverage

        Service.startService(self)

        # this doesn't change the shell's notion of the environment, but it make
        # the test in process_startup() succeed, which is the goal here.
        os.environ["COVERAGE_PROCESS_START"] = ".coveragerc"

        # maybe-start the global coverage, unless it already got started
        self.cov = coverage.process_startup()
        if self.cov is None:
            self.cov = coverage.process_startup.coverage

    def stopService(self):
        """
        Make sure that coverage has stopped; internally, it depends on ataxit
        handlers running which doesn't always happen (Twisted's shutdown hook
        also won't run if os._exit() is called, but it runs more-often than
        atexit handlers).
        """
        self.cov.stop()
        self.cov.save()


def coverage_service():
    """
    Return a service which will arrange for coverage to be collected (or fail
    if the ``coverage`` package is not installed).
    """
    return _CoverageService()
