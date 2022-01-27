# Copyright (c) Least Authority TFA GmbH.
# See COPYING.* for details.

"""
The unit test package for Magic Folder.

This also does some test-only related setup.  The expectation is that this
code will never be loaded under real usage.
"""

from sys import (
    stderr,
)

from foolscap.logging.incident import IncidentQualifier


class NonQualifier(IncidentQualifier, object):
    def check_event(self, ev):
        return False

def disable_foolscap_incidents():
    # Foolscap-0.2.9 (at least) uses "trailing delay" in its default incident
    # reporter: after a severe log event is recorded (thus triggering an
    # "incident" in which recent events are dumped to a file), a few seconds
    # of subsequent events are also recorded in the incident file. The timer
    # that this leaves running will cause "Unclean Reactor" unit test
    # failures. The simplest workaround is to disable this timer. Note that
    # this disables the timer for the entire process: do not call this from
    # regular runtime code; only use it for unit tests that are running under
    # Trial.
    #IncidentReporter.TRAILING_DELAY = None
    #
    # Also, using Incidents more than doubles the test time. So we just
    # disable them entirely.
    from foolscap.logging.log import theLogger
    iq = NonQualifier()
    theLogger.setIncidentQualifier(iq)

# we disable incident reporting for all unit tests.
disable_foolscap_incidents()


def _configure_hypothesis():
    from os import environ

    from hypothesis import (
        HealthCheck,
        settings,
    )

    # if you add more profiles here, note that profile names aren't
    # namespaced in any way and Hypothesis allows profile name
    # collisions to pass silently, then more or less randomly picks
    # one definition to use when you try to activate the name. So
    # please prefix any other profiles in here with "magic-folder-"
    # for a somewhat lower chance of collision.

    settings.register_profile(
        "magic-folder-fast",
        max_examples=1,
        # see magic-folder-ci profile below for justification
        suppress_health_check=[
            HealthCheck.too_slow,
        ],
        deadline=60*10*1000,  # _some_ number that's not "forever" (milliseconds)
    )

    settings.register_profile(
        "magic-folder-ci",
        suppress_health_check=[
            # CPU resources available to CI builds typically varies
            # significantly from run to run making it difficult to determine
            # if "too slow" data generation is a result of the code or the
            # execution environment.  Prevent these checks from
            # (intermittently) failing tests that are otherwise fine.
            HealthCheck.too_slow,
        ],
        # With the same reasoning, disable the test deadline.
        deadline=60*10*1000,  # _some_ number that's not "forever" (milliseconds)
    )

    profile_name = environ.get("MAGIC_FOLDER_HYPOTHESIS_PROFILE", "default")
    print("Loading Hypothesis profile {}".format(profile_name), file=stderr)
    settings.load_profile(profile_name)
_configure_hypothesis()

from eliot import to_file
to_file(open("eliot.log", "w", encoding="utf8"))
