"""
Post-process pip-compile-multi output to handle platform specific dependencies.

pip-compile (and thus pip-compile-multi) don't support generating lock files
for platforms other than the current one. They suggest[1] generating a lock
file for each environment seperately.

However, the only platform-specific (transitive) dependencies are
colorama and pywin32 -- and now several others :(. To avoid having to
maintain separate sets of lock files per-platform, we modify the
generated lock files to the platform specific dependencies (using
environment markers). This is based loosely on an idea from [2].

We have a hand-generated platform-specific requirements lockfile with appropriate
environment markers. After generating a lock file with pip-compile-multi, we
add a reference to the platform-specific requirements lockfile.

[1] https://github.com/jazzband/pip-tools#cross-environment-usage-of-requirementsinrequirementstxt-and-pip-compile
[2] https://github.com/jazzband/pip-tools/issues/826#issuecomment-748459788
"""
from __future__ import absolute_import, division, print_function, unicode_literals

from twisted.python import usage
from twisted.python.filepath import FilePath


class Options(usage.Options):
    synopsis = "Usage: platform-pins.py [--remove] BASE_REQUIREMENTS PLATFORM_REQUIREMENTS"
    optFlags = [
        ("remove", "", "Remove platform pins."),
    ]
    def parseArgs(self, base_requirements, platform_requirements):
        self["base_requirements"] = FilePath(base_requirements)
        self["platform_requirements"] = FilePath(platform_requirements)


HEADER = """
### THIS IS A GENERATED FILE
#
# Include pinned platform dependencies as neither pip-compile nor
# pip-compile-multi handles them.
# See https://github.com/jazzband/pip-tools/issues/826#issuecomment-748459788
# Run 'tox -e pin-requirements' to regenerate this file.
""".strip()


def main(base_requirements, platform_requirements, remove):
    if base_requirements.parent() != platform_requirements.parent():
        print("ERROR: Requirement files must be in the same directory.")
        raise SystemExit(1)

    original_reqs = base_requirements.getContent().decode("utf8")
    if remove:
        lines = original_reqs.splitlines()
        # pip-compile-multi generates files with this header
        # strip everything before it
        while not lines[0].startswith("# SHA1:"):
            lines.pop(0)
        new_reqs = "\n".join(lines) + "\n"
    else:
        new_reqs = "\n".join([HEADER, "-r {}".format(platform_requirements.basename()), original_reqs])

    with base_requirements.open("w") as f:
        f.write(new_reqs.encode("utf8"))


if __name__ == "__main__":
    import sys
    config = Options()
    try:
        config.parseOptions()
    except usage.UsageError as e:
        print('{}: {}'.format(sys.argv[0], e))
        raise SystemExit(1)
    main(**config)
