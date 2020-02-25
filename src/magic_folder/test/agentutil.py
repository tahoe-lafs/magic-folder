# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Testing helpers related to Twisted's ``IAgent``.
"""

from zope.interface import (
    implementer,
)
from zope.interface.verify import (
    verifyClass,
)

import attr
import attr.validators

from twisted.internet.defer import (
    fail,
)

from twisted.python.failure import (
    Failure,
)

from twisted.web.iweb import (
    IAgent,
)

@implementer(IAgent)
@attr.s
class FailingAgent(object):
    """
    An ``IAgent`` implementation which returns failures for every request
    attempt.

    :ivar Failure reason: The reason to give for every failure.
    """
    reason = attr.ib(validator=attr.validators.instance_of(Failure))

    def request(self, method, url, headers=None, bodyProducer=None):
        return fail(self.reason)

verifyClass(IAgent, FailingAgent)
