# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Eliot logging utility imported from Tahoe-LAFS code.
"""

import inspect
import json
import os

from eliot import (
    Action,
    Field,
    FileDestination,
    ILogger,
    Message,
    ValidationError,
    add_destinations,
    remove_destination,
    start_action,
    start_task,
    write_traceback,
)
from eliot.twisted import inline_callbacks

from logging import (
    INFO,
    Handler,
    getLogger,
)

from eliot.twisted import (
    DeferredContext,
)
from twisted.logger import (
    ILogObserver,
    eventAsJSON,
    globalLogPublisher,
)

from twisted.internet.defer import (
    maybeDeferred,
)
from twisted.python import usage

from functools import wraps
from twisted.python.filepath import (
    FilePath,
)
from twisted.application.service import Service
from zope.interface import (
    implementer,
)

import attr
from attr.validators import (
    optional,
    provides,
)

from json import loads
from inspect import unwrap


def validateInstanceOf(t):
    """
    Return an Eliot validator that requires values to be instances of ``t``.
    """
    def validator(v):
        if not isinstance(v, t):
            raise ValidationError("{} not an instance of {}".format(v, t))
    return validator


RELPATH = Field.for_types(
    u"relpath",
    [str],
    u"The relative path of a file in a magic-folder.",
)

ABSPATH = Field(
    u"abspath",
    lambda fp: fp.path,
    u"The absolute path of a file in a magic-folder.",
    validateInstanceOf(FilePath),
)

VERSION = Field.for_types(
    u"version",
    [int],
    u"The version of the file.",
)

LAST_UPLOADED_URI = Field.for_types(
    u"last_uploaded_uri",
    [str, bytes, None],
    u"The filecap to which this version of this file was uploaded.",
)

LAST_DOWNLOADED_URI = Field.for_types(
    u"last_downloaded_uri",
    [str, bytes, None],
    u"The filecap from which the previous version of this file was downloaded.",
)

LAST_DOWNLOADED_TIMESTAMP = Field.for_types(
    u"last_downloaded_timestamp",
    (float, int),
    u"(XXX probably not really, don't trust this) The timestamp of the last download of this file.",
)


def validateSetMembership(s):
    """
    Return an Eliot validator that requires values to be elements of ``s``.
    """
    def validator(v):
        if v not in s:
            raise ValidationError("{} not in {}".format(v, s))
    return validator


def opt_eliot_fd(self, fd):
    """
    File descriptor to send log eliot to.
    """
    try:
        fd = int(fd)
    except Exception as e:
        raise usage.UsageError(str(e))

    stdio_fds = {
        1: self.stdout,
        2: self.stderr,
    }

    def to_fd(reactor):
        f = stdio_fds.get(fd)
        if f is None:
            f = os.fdopen(fd, "w")
        return FileDestination(f)

    self.setdefault("eliot-destinations", []).append(to_fd)


def opt_eliot_task_fields(self, task_fields):
    """
    Wrap all logs in a task with given (JSON) fields. (for testing)
    """
    # JSON of fields to use for a top-level eliot task. If this is specified, a
    # global eliot task will be created, and all eliot logs will be children of
    # the that task. This is intened to help provide context to eliot logs,
    # when they are captured in the test's eliot logs.
    try:
        task_fields = json.loads(task_fields)
    except Exception as e:
        raise usage.UsageError(str(e))
    self.setdefault("eliot-task-fields", {}).update(task_fields)


def with_eliot_options(cls):
    cls.opt_eliot_fd = opt_eliot_fd
    cls.opt_eliot_task_fields = opt_eliot_task_fields
    return cls


def maybe_enable_eliot_logging(options):
    destinations = options.get("eliot-destinations")
    task_fields = options.get("eliot-task-fields")
    if not destinations:
        return
    from twisted.internet import reactor

    destinations = [destination(reactor) for destination in destinations]
    service = _EliotLogging(destinations, task_fields)
    service.startService()
    reactor.addSystemEventTrigger("after", "shutdown", service.stopService)


@attr.s
class _EliotLogging(Service):
    """
    A service which adds stdout as an Eliot destination while it is running.

    :ivar list[Callable[[IReactorCore], eliot.IDestination] destinations:
       The Eliot destinations which will is added by this service.
    """

    destinations = attr.ib(
        validator=attr.validators.deep_iterable(attr.validators.is_callable())
    )
    task_fields = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(dict))
    )
    task = attr.ib(
        init=False,
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(Action)),
    )

    # Currently, a major use of eliot logging is for integration testing.
    # The twisted logs captured this way are *very* verbose, so they are disabled.
    # We should revisit the format and renable them.
    # https://github.com/LeastAuthority/magic-folder/issues/453
    capture_logs = attr.ib(default=False, validator=attr.validators.instance_of(bool))

    def startService(self):
        if self.task_fields:
            self.task = start_task(**self.task_fields)
            self.task.__enter__()
        if self.capture_logs:
            self.stdlib_cleanup = _stdlib_logging_to_eliot_configuration(getLogger())
            self.twisted_observer = _TwistedLoggerToEliotObserver()
            globalLogPublisher.addObserver(self.twisted_observer)
        add_destinations(*self.destinations)
        return Service.startService(self)

    def stopService(self):
        if self.capture_logs:
            globalLogPublisher.removeObserver(self.twisted_observer)
            self.stdlib_cleanup()
        if self.task is not None:
            self.task.finish()
        for dest in self.destinations:
            remove_destination(dest)
        return Service.stopService(self)


@implementer(ILogObserver)
@attr.s(frozen=True)
class _TwistedLoggerToEliotObserver(object):
    """
    An ``ILogObserver`` which re-publishes events as Eliot messages.
    """
    logger = attr.ib(default=None, validator=optional(provides(ILogger)))

    def _observe(self, event):
        flattened = loads(eventAsJSON(event))
        # We get a timestamp from Eliot.
        flattened.pop(u"log_time")
        # This is never serializable anyway.  "Legacy" log events (from
        # twisted.python.log) don't have this so make it optional.
        flattened.pop(u"log_logger", None)

        Message.new(
            message_type=u"eliot:twisted",
            **flattened
        ).write(self.logger)


    # The actual ILogObserver interface uses this.
    __call__ = _observe


class _StdlibLoggingToEliotHandler(Handler):
    def __init__(self, logger=None):
        Handler.__init__(self)
        self.logger = logger

    def emit(self, record):
        Message.new(
            message_type=u"eliot:stdlib",
            log_level=record.levelname,
            logger=record.name,
            message=record.getMessage()
        ).write(self.logger)

        if record.exc_info:
            write_traceback(
                logger=self.logger,
                exc_info=record.exc_info,
            )


def _stdlib_logging_to_eliot_configuration(stdlib_logger, eliot_logger=None):
    """
    Add a handler to ``stdlib_logger`` which will relay events to
    ``eliot_logger`` (or the default Eliot logger if ``eliot_logger`` is
    ``None``).
    """
    handler = _StdlibLoggingToEliotHandler(eliot_logger)
    handler.set_name(u"eliot")
    handler.setLevel(INFO)
    stdlib_logger.addHandler(handler)
    return lambda: stdlib_logger.removeHandler(handler)


def log_call_deferred(action_type, include_args=False):
    """
    Like ``eliot.log_call`` but for functions which return ``Deferred``.
    """

    if include_args:
        if include_args is True:
            arg_filter = lambda k: k not in {"self", "reactor"}
        else:
            include_args = set(include_args)
            arg_filter = lambda k: k in include_args

    def decorate_log_call_deferred(f):
        wrapped_f = unwrap(f)

        @wraps(f)
        def logged_f(*a, **kw):
            if include_args:
                callargs = {
                    k: v
                    for k, v in inspect.getcallargs(wrapped_f, *a, **kw).items()
                    if arg_filter(k)
                }
            else:
                callargs = {}

            # Use the action's context method to avoid ending the action when
            # the `with` block ends.
            with start_action(action_type=action_type, **callargs).context():
                # Use addActionFinish so that the action finishes when the
                # Deferred fires.
                d = maybeDeferred(f, *a, **kw)
                return DeferredContext(d).addActionFinish()

        return logged_f

    return decorate_log_call_deferred


def log_inline_callbacks(action_type, include_args=False):
    """
    Like py:`log_call_deferred` but decorates the function with :py:`inline_callbacks`.

    This is needed so that py:`log_call_deferred` can access the right argument names.
    """

    def wrap(f):
        wrapper = inline_callbacks(f)
        # __wrapped__ is introduced in python 3.2
        wrapper.__wrapped__ = f
        return log_call_deferred(action_type, include_args=include_args)(wrapper)

    return wrap
