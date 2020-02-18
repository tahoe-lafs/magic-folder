# Copyright 2020 Least Authority TFA GmbH
# See COPYING for details.

"""
Testing helpers related to Twisted's ``IAgent``.
"""

from zope.interface import (
    Interface,
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
    reason = attr.ib(validator=attr.validators.instance_of(Failure))

    def request(self, method, url, headers=None, bodyProducer=None):
        return fail(self.reason)
