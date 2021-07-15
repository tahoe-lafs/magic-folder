from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import json
import attr


@attr.s
class PublicError(Exception):
    """
    Any sort of error that is permissable to show to a UI.

    Such an error MUST NOT reveal any secret information, which
    includes at least: any capability-string or any fURL.

    The langauge used in the error should be plain and simple,
    avoiding jargon and technical details (except where immediately
    relevant). This is used by the IStatus API.
    """
    timestamp = attr.ib(validator=attr.validators.instance_of(float))
    summary = attr.ib(validator=attr.validators.instance_of(unicode))

    def to_json(self):
        """
        :returns: a dict suitable for serializing to JSON
        """
        return {
            "timestamp": int(self.timestamp),
            "summary": self.summary,
        }

    def __str__(self):
        return self.summary


def public_error_tahoe_failure(summary):
    return PublicError(summary)
