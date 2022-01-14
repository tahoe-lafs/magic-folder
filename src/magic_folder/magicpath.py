import re

import attr


@attr.s(auto_exc=True)
class InvalidMangledPath(Exception):
    path = attr.ib()
    sequence = attr.ib()

    def __str__(self):
        return "Invalid escape sequence {!r} in {!r}.".format(self.sequence, self.path)

def path2magic(path):
    return re.sub(u'[/@]', lambda m: {u'/': u'@_', u'@': u'@@'}[m.group(0)], path)

def magic2path(path):
    try:
        return re.sub(u'@.', lambda m: {u'@_': u'/', u'@@': u'@'}[m.group(0)], path)
    except KeyError as e:
        raise InvalidMangledPath(path, str(e))
