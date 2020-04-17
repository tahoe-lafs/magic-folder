from eliot import (
    Field,
    ValidationError,
)


PathInfo = namedtuple('PathInfo', 'isdir isfile islink exists size mtime_ns ctime_ns')


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
    [unicode],
    u"The relative path of a file in a magic-folder.",
)

VERSION = Field.for_types(
    u"version",
    [int, long],
    u"The version of the file.",
)

LAST_UPLOADED_URI = Field.for_types(
    u"last_uploaded_uri",
    [unicode, bytes, None],
    u"The filecap to which this version of this file was uploaded.",
)

LAST_DOWNLOADED_URI = Field.for_types(
    u"last_downloaded_uri",
    [unicode, bytes, None],
    u"The filecap from which the previous version of this file was downloaded.",
)

LAST_DOWNLOADED_TIMESTAMP = Field.for_types(
    u"last_downloaded_timestamp",
    [float, int, long],
    u"(XXX probably not really, don't trust this) The timestamp of the last download of this file.",
)

PATHINFO = Field(
    u"pathinfo",
    lambda v: None if v is None else {
        "isdir": v.isdir,
        "isfile": v.isfile,
        "islink": v.islink,
        "exists": v.exists,
        "size": v.size,
        "mtime_ns": v.mtime_ns,
        "ctime_ns": v.ctime_ns,
    },
    u"The metadata for this version of this file.",
    validateInstanceOf((type(None), PathInfo)),
)


def validateSetMembership(s):
    """
    Return an Eliot validator that requires values to be elements of ``s``.
    """
    def validator(v):
        if v not in s:
            raise ValidationError("{} not in {}".format(v, s))
    return validator
