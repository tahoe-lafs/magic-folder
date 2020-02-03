from __future__ import print_function

import os.path, re, fnmatch
from twisted.python import usage
from allmydata.scripts.common import get_aliases, get_default_nodedir, \
     DEFAULT_ALIAS, BaseOptions
from allmydata.util.encodingutil import argv_to_unicode, argv_to_abspath, quote_local_unicode_path
from .tahoe_status import TahoeStatusCommand

NODEURL_RE=re.compile("http(s?)://([^:]*)(:([1-9][0-9]*))?")

_default_nodedir = get_default_nodedir()

class FileStoreOptions(BaseOptions):
    optParameters = [
        ["node-url", "u", None,
         "Specify the URL of the Tahoe gateway node, such as "
         "'http://127.0.0.1:3456'. "
         "This overrides the URL found in the --node-directory ."],
        ["dir-cap", None, None,
         "Specify which dirnode URI should be used as the 'tahoe' alias."]
        ]

    def postOptions(self):
        self["quiet"] = self.parent["quiet"]
        if self.parent['node-directory']:
            self['node-directory'] = argv_to_abspath(self.parent['node-directory'])
        else:
            self['node-directory'] = _default_nodedir

        # compute a node-url from the existing options, put in self['node-url']
        if self['node-url']:
            if (not isinstance(self['node-url'], basestring)
                or not NODEURL_RE.match(self['node-url'])):
                msg = ("--node-url is required to be a string and look like "
                       "\"http://HOSTNAMEORADDR:PORT\", not: %r" %
                       (self['node-url'],))
                raise usage.UsageError(msg)
        else:
            node_url_file = os.path.join(self['node-directory'], "node.url")
            self['node-url'] = open(node_url_file, "r").read().strip()
        if self['node-url'][-1] != "/":
            self['node-url'] += "/"

        aliases = get_aliases(self['node-directory'])
        if self['dir-cap']:
            aliases[DEFAULT_ALIAS] = self['dir-cap']
        self.aliases = aliases # maps alias name to dircap


class MakeDirectoryOptions(FileStoreOptions):
    optParameters = [
        ("format", None, None, "Create a directory with the given format: SDMF or MDMF (case-insensitive)"),
        ]

    def parseArgs(self, where=""):
        self.where = argv_to_unicode(where)

        if self['format']:
            if self['format'].upper() not in ("SDMF", "MDMF"):
                raise usage.UsageError("%s is an invalid format" % self['format'])

    synopsis = "[options] [REMOTE_DIR]"
    description = """Create a new directory, either unlinked or as a subdirectory."""

class AddAliasOptions(FileStoreOptions):
    def parseArgs(self, alias, cap):
        self.alias = argv_to_unicode(alias)
        if self.alias.endswith(u':'):
            self.alias = self.alias[:-1]
        self.cap = cap

    synopsis = "[options] ALIAS[:] DIRCAP"
    description = """Add a new alias for an existing directory."""

class CreateAliasOptions(FileStoreOptions):
    def parseArgs(self, alias):
        self.alias = argv_to_unicode(alias)
        if self.alias.endswith(u':'):
            self.alias = self.alias[:-1]

    synopsis = "[options] ALIAS[:]"
    description = """Create a new directory and add an alias for it."""

class ListAliasesOptions(FileStoreOptions):
    synopsis = "[options]"
    description = """Display a table of all configured aliases."""
    optFlags = [
        ("readonly-uri", None, "Show read-only dircaps instead of readwrite"),
        ("json", None, "Show JSON output"),
    ]

class ListOptions(FileStoreOptions):
    optFlags = [
        ("long", "l", "Use long format: show file sizes, and timestamps."),
        ("uri", None, "Show file/directory URIs."),
        ("readonly-uri", None, "Show read-only file/directory URIs."),
        ("classify", "F", "Append '/' to directory names, and '*' to mutable."),
        ("json", None, "Show the raw JSON output."),
        ]
    def parseArgs(self, where=""):
        self.where = argv_to_unicode(where)

    synopsis = "[options] [PATH]"

    description = """
    List the contents of some portion of the grid.

    If PATH is omitted, "tahoe:" is assumed.

    When the -l or --long option is used, each line is shown in the
    following format:

     drwx <size> <date/time> <name in this directory>

    where each of the letters on the left may be replaced by '-'.
    If 'd' is present, it indicates that the object is a directory.
    If the 'd' is replaced by a '?', the object type is unknown.
    'rwx' is a Unix-like permissions mask: if the mask includes 'w',
    then the object is writeable through its link in this directory
    (note that the link might be replaceable even if the object is
    not writeable through the current link).
    The 'x' is a legacy of Unix filesystems. In Tahoe it is used
    only to indicate that the contents of a directory can be listed.

    Directories have no size, so their size field is shown as '-'.
    Otherwise the size of the file, when known, is given in bytes.
    The size of mutable files or unknown objects is shown as '?'.

    The date/time shows when this link in the Tahoe grid was last
    modified.
    """

class GetOptions(FileStoreOptions):
    def parseArgs(self, arg1, arg2=None):
        # tahoe get FOO |less            # write to stdout
        # tahoe get tahoe:FOO |less      # same
        # tahoe get FOO bar              # write to local file
        # tahoe get tahoe:FOO bar        # same

        if arg2 == "-":
            arg2 = None

        self.from_file = argv_to_unicode(arg1)
        self.to_file   = None if arg2 is None else argv_to_abspath(arg2)

    synopsis = "[options] REMOTE_FILE LOCAL_FILE"

    description = """
    Retrieve a file from the grid and write it to the local filesystem. If
    LOCAL_FILE is omitted or '-', the contents of the file will be written to
    stdout."""

    description_unwrapped = """
    Examples:
     % tahoe get FOO |less            # write to stdout
     % tahoe get tahoe:FOO |less      # same
     % tahoe get FOO bar              # write to local file
     % tahoe get tahoe:FOO bar        # same
    """

class PutOptions(FileStoreOptions):
    optFlags = [
        ("mutable", "m", "Create a mutable file instead of an immutable one (like --format=SDMF)"),
        ]
    optParameters = [
        ("format", None, None, "Create a file with the given format: SDMF and MDMF for mutable, CHK (default) for immutable. (case-insensitive)"),
        ]

    def parseArgs(self, arg1=None, arg2=None):
        # see Examples below

        if arg1 == "-":
            arg1 = None

        self.from_file = None if arg1 is None else argv_to_abspath(arg1)
        self.to_file   = None if arg2 is None else argv_to_unicode(arg2)

        if self['format']:
            if self['format'].upper() not in ("SDMF", "MDMF", "CHK"):
                raise usage.UsageError("%s is an invalid format" % self['format'])

    synopsis = "[options] LOCAL_FILE REMOTE_FILE"

    description = """
    Put a file into the grid, copying its contents from the local filesystem.
    If REMOTE_FILE is missing, upload the file but do not link it into a
    directory; also print the new filecap to stdout. If LOCAL_FILE is missing
    or '-', data will be copied from stdin. REMOTE_FILE is assumed to start
    with tahoe: unless otherwise specified.

    If the destination file already exists and is mutable, it will be
    modified in-place, whether or not --mutable is specified. (--mutable only
    affects creation of new files.)
    """

    description_unwrapped = """
    Examples:
     % cat FILE | tahoe put                # create unlinked file from stdin
     % cat FILE | tahoe put -              # same
     % tahoe put bar                       # create unlinked file from local 'bar'
     % cat FILE | tahoe put - FOO          # create tahoe:FOO from stdin
     % tahoe put bar FOO                   # copy local 'bar' to tahoe:FOO
     % tahoe put bar tahoe:FOO             # same
     % tahoe put bar MUTABLE-FILE-WRITECAP # modify the mutable file in-place
    """

class CpOptions(FileStoreOptions):
    optFlags = [
        ("recursive", "r", "Copy source directory recursively."),
        ("verbose", "v", "Be noisy about what is happening."),
        ("caps-only", None,
         "When copying to local files, write out filecaps instead of actual "
         "data (only useful for debugging and tree-comparison purposes)."),
        ]

    def parseArgs(self, *args):
        if len(args) < 2:
            raise usage.UsageError("cp requires at least two arguments")
        self.sources = map(argv_to_unicode, args[:-1])
        self.destination = argv_to_unicode(args[-1])

    synopsis = "[options] FROM.. TO"

    description = """
    Use 'tahoe cp' to copy files between a local filesystem and a Tahoe grid.
    Any FROM/TO arguments that begin with an alias indicate Tahoe-side
    files or non-file arguments. Directories will be copied recursively.
    New Tahoe-side directories will be created when necessary. Assuming that
    you have previously set up an alias 'home' with 'tahoe create-alias home',
    here are some examples:

     tahoe cp ~/foo.txt home:  # creates tahoe-side home:foo.txt

     tahoe cp ~/foo.txt /tmp/bar.txt home:  # copies two files to home:

     tahoe cp ~/Pictures home:stuff/my-pictures  # copies directory recursively

    You can also use a dircap as either FROM or TO target:

     tahoe cp URI:DIR2-RO:ixqhc4kdbjxc7o65xjnveoewym:5x6lwoxghrd5rxhwunzavft2qygfkt27oj3fbxlq4c6p45z5uneq/blog.html ./   # copy Zooko's wiki page to a local file

    This command still has some limitations: symlinks and special files
    (device nodes, named pipes) are not handled very well. Arguments should
    not have trailing slashes (they are ignored for directory arguments, but
    trigger errors for file arguments). When copying directories, it can be
    unclear whether you mean to copy the contents of a source directory, or
    the source directory itself (i.e. whether the output goes under the
    target directory, or one directory lower). Tahoe's rule is that source
    directories with names are referring to the directory as a whole, and
    source directories without names (e.g. a raw dircap) are referring to the
    contents.
    """

class UnlinkOptions(FileStoreOptions):
    def parseArgs(self, where):
        self.where = argv_to_unicode(where)

    synopsis = "[options] REMOTE_FILE"
    description = "Remove a named file from its parent directory."

class MvOptions(FileStoreOptions):
    def parseArgs(self, frompath, topath):
        self.from_file = argv_to_unicode(frompath)
        self.to_file = argv_to_unicode(topath)

    synopsis = "[options] FROM TO"

    description = """
    Use 'tahoe mv' to move files that are already on the grid elsewhere on
    the grid, e.g., 'tahoe mv alias:some_file alias:new_file'.

    If moving a remote file into a remote directory, you'll need to append a
    '/' to the name of the remote directory, e.g., 'tahoe mv tahoe:file1
    tahoe:dir/', not 'tahoe mv tahoe:file1 tahoe:dir'.

    Note that it is not possible to use this command to move local files to
    the grid -- use 'tahoe cp' for that.
    """

class LnOptions(FileStoreOptions):
    def parseArgs(self, frompath, topath):
        self.from_file = argv_to_unicode(frompath)
        self.to_file = argv_to_unicode(topath)

    synopsis = "[options] FROM_LINK TO_LINK"

    description = """
    Use 'tahoe ln' to duplicate a link (directory entry) already on the grid
    to elsewhere on the grid. For example 'tahoe ln alias:some_file
    alias:new_file'. causes 'alias:new_file' to point to the same object that
    'alias:some_file' points to.

    (The argument order is the same as Unix ln. To remember the order, you
    can think of this command as copying a link, rather than copying a file
    as 'tahoe cp' does. Then the argument order is consistent with that of
    'tahoe cp'.)

    When linking a remote file into a remote directory, you'll need to append
    a '/' to the name of the remote directory, e.g. 'tahoe ln tahoe:file1
    tahoe:dir/' (which is shorthand for 'tahoe ln tahoe:file1
    tahoe:dir/file1'). If you forget the '/', e.g. 'tahoe ln tahoe:file1
    tahoe:dir', the 'ln' command will refuse to overwrite the 'tahoe:dir'
    directory, and will exit with an error.

    Note that it is not possible to use this command to create links between
    local and remote files.
    """

class BackupConfigurationError(Exception):
    pass

class BackupOptions(FileStoreOptions):
    optFlags = [
        ("verbose", "v", "Be noisy about what is happening."),
        ("ignore-timestamps", None, "Do not use backupdb timestamps to decide whether a local file is unchanged."),
        ]

    vcs_patterns = ('CVS', 'RCS', 'SCCS', '.git', '.gitignore', '.cvsignore',
                    '.svn', '.arch-ids','{arch}', '=RELEASE-ID',
                    '=meta-update', '=update', '.bzr', '.bzrignore',
                    '.bzrtags', '.hg', '.hgignore', '_darcs')

    def __init__(self):
        super(BackupOptions, self).__init__()
        self['exclude'] = set()

    def parseArgs(self, localdir, topath):
        self.from_dir = argv_to_abspath(localdir)
        self.to_dir = argv_to_unicode(topath)

    synopsis = "[options] FROM ALIAS:TO"

    def opt_exclude(self, pattern):
        """Ignore files matching a glob pattern. You may give multiple
        '--exclude' options."""
        g = argv_to_unicode(pattern).strip()
        if g:
            exclude = self['exclude']
            exclude.add(g)

    def opt_exclude_from(self, filepath):
        """Ignore file matching glob patterns listed in file, one per
        line. The file is assumed to be in the argv encoding."""
        abs_filepath = argv_to_abspath(filepath)
        try:
            exclude_file = file(abs_filepath)
        except:
            raise BackupConfigurationError('Error opening exclude file %s.' % quote_local_unicode_path(abs_filepath))
        try:
            for line in exclude_file:
                self.opt_exclude(line)
        finally:
            exclude_file.close()

    def opt_exclude_vcs(self):
        """Exclude files and directories used by following version control
        systems: CVS, RCS, SCCS, Git, SVN, Arch, Bazaar(bzr), Mercurial,
        Darcs."""
        for pattern in self.vcs_patterns:
            self.opt_exclude(pattern)

    def filter_listdir(self, listdir):
        """Yields non-excluded childpaths in path."""
        exclude = self['exclude']
        exclude_regexps = [re.compile(fnmatch.translate(pat)) for pat in exclude]
        for filename in listdir:
            for regexp in exclude_regexps:
                if regexp.match(filename):
                    break
            else:
                yield filename

    description = """
    Add a versioned backup of the local FROM directory to a timestamped
    subdirectory of the TO/Archives directory on the grid, sharing as many
    files and directories as possible with earlier backups. Create TO/Latest
    as a reference to the latest backup. Behaves somewhat like 'rsync -a
    --link-dest=TO/Archives/(previous) FROM TO/Archives/(new); ln -sf
    TO/Archives/(new) TO/Latest'."""

class WebopenOptions(FileStoreOptions):
    optFlags = [
        ("info", "i", "Open the t=info page for the file"),
        ]
    def parseArgs(self, where=''):
        self.where = argv_to_unicode(where)

    synopsis = "[options] [ALIAS:PATH]"

    description = """
    Open a web browser to the contents of some file or
    directory on the grid. When run without arguments, open the Welcome
    page."""

class ManifestOptions(FileStoreOptions):
    optFlags = [
        ("storage-index", "s", "Only print storage index strings, not pathname+cap."),
        ("verify-cap", None, "Only print verifycap, not pathname+cap."),
        ("repair-cap", None, "Only print repaircap, not pathname+cap."),
        ("raw", "r", "Display raw JSON data instead of parsed."),
        ]
    def parseArgs(self, where=''):
        self.where = argv_to_unicode(where)

    synopsis = "[options] [ALIAS:PATH]"
    description = """
    Print a list of all files and directories reachable from the given
    starting point."""

class StatsOptions(FileStoreOptions):
    optFlags = [
        ("raw", "r", "Display raw JSON data instead of parsed"),
        ]
    def parseArgs(self, where=''):
        self.where = argv_to_unicode(where)

    synopsis = "[options] [ALIAS:PATH]"
    description = """
    Print statistics about of all files and directories reachable from the
    given starting point."""

class CheckOptions(FileStoreOptions):
    optFlags = [
        ("raw", None, "Display raw JSON data instead of parsed."),
        ("verify", None, "Verify all hashes, instead of merely querying share presence."),
        ("repair", None, "Automatically repair any problems found."),
        ("add-lease", None, "Add/renew lease on all shares."),
        ]
    def parseArgs(self, *locations):
        self.locations = map(argv_to_unicode, locations)

    synopsis = "[options] [ALIAS:PATH]"
    description = """
    Check a single file or directory: count how many shares are available and
    verify their hashes. Optionally repair the file if any problems were
    found."""

class DeepCheckOptions(FileStoreOptions):
    optFlags = [
        ("raw", None, "Display raw JSON data instead of parsed."),
        ("verify", None, "Verify all hashes, instead of merely querying share presence."),
        ("repair", None, "Automatically repair any problems found."),
        ("add-lease", None, "Add/renew lease on all shares."),
        ("verbose", "v", "Be noisy about what is happening."),
        ]
    def parseArgs(self, *locations):
        self.locations = map(argv_to_unicode, locations)

    synopsis = "[options] [ALIAS:PATH]"
    description = """
    Check all files and directories reachable from the given starting point
    (which must be a directory), like 'tahoe check' but for multiple files.
    Optionally repair any problems found."""

subCommands = [
    ["mkdir", None, MakeDirectoryOptions, "Create a new directory."],
    ["add-alias", None, AddAliasOptions, "Add a new alias cap."],
    ["create-alias", None, CreateAliasOptions, "Create a new alias cap."],
    ["list-aliases", None, ListAliasesOptions, "List all alias caps."],
    ["ls", None, ListOptions, "List a directory."],
    ["get", None, GetOptions, "Retrieve a file from the grid."],
    ["put", None, PutOptions, "Upload a file into the grid."],
    ["cp", None, CpOptions, "Copy one or more files or directories."],
    ["unlink", None, UnlinkOptions, "Unlink a file or directory on the grid."],
    ["mv", None, MvOptions, "Move a file within the grid."],
    ["ln", None, LnOptions, "Make an additional link to an existing file or directory."],
    ["backup", None, BackupOptions, "Make target dir look like local dir."],
    ["webopen", None, WebopenOptions, "Open a web browser to a grid file or directory."],
    ["manifest", None, ManifestOptions, "List all files/directories in a subtree."],
    ["stats", None, StatsOptions, "Print statistics about all files/directories in a subtree."],
    ["check", None, CheckOptions, "Check a single file or directory."],
    ["deep-check", None, DeepCheckOptions, "Check all files/directories reachable from a starting point."],
    ["status", None, TahoeStatusCommand, "Various status information."],
    ]

def mkdir(options):
    from allmydata.scripts import tahoe_mkdir
    rc = tahoe_mkdir.mkdir(options)
    return rc

def add_alias(options):
    from allmydata.scripts import tahoe_add_alias
    rc = tahoe_add_alias.add_alias(options)
    return rc

def create_alias(options):
    from allmydata.scripts import tahoe_add_alias
    rc = tahoe_add_alias.create_alias(options)
    return rc

def list_aliases(options):
    from allmydata.scripts import tahoe_add_alias
    rc = tahoe_add_alias.list_aliases(options)
    return rc

def list(options):
    from allmydata.scripts import tahoe_ls
    rc = tahoe_ls.list(options)
    return rc

def get(options):
    from allmydata.scripts import tahoe_get
    rc = tahoe_get.get(options)
    if rc == 0:
        if options.to_file is None:
            # be quiet, since the file being written to stdout should be
            # proof enough that it worked, unless the user is unlucky
            # enough to have picked an empty file
            pass
        else:
            print("%s retrieved and written to %s" % \
                  (options.from_file, options.to_file), file=options.stderr)
    return rc

def put(options):
    from allmydata.scripts import tahoe_put
    rc = tahoe_put.put(options)
    return rc

def cp(options):
    from allmydata.scripts import tahoe_cp
    rc = tahoe_cp.copy(options)
    return rc

def unlink(options, command="unlink"):
    from allmydata.scripts import tahoe_unlink
    rc = tahoe_unlink.unlink(options, command=command)
    return rc

def rm(options):
    return unlink(options, command="rm")

def mv(options):
    from allmydata.scripts import tahoe_mv
    rc = tahoe_mv.mv(options, mode="move")
    return rc

def ln(options):
    from allmydata.scripts import tahoe_mv
    rc = tahoe_mv.mv(options, mode="link")
    return rc

def backup(options):
    from allmydata.scripts import tahoe_backup
    rc = tahoe_backup.backup(options)
    return rc

def webopen(options, opener=None):
    from allmydata.scripts import tahoe_webopen
    rc = tahoe_webopen.webopen(options, opener=opener)
    return rc

def manifest(options):
    from allmydata.scripts import tahoe_manifest
    rc = tahoe_manifest.manifest(options)
    return rc

def stats(options):
    from allmydata.scripts import tahoe_manifest
    rc = tahoe_manifest.stats(options)
    return rc

def check(options):
    from allmydata.scripts import tahoe_check
    rc = tahoe_check.check(options)
    return rc

def deepcheck(options):
    from allmydata.scripts import tahoe_check
    rc = tahoe_check.deepcheck(options)
    return rc

def status(options):
    from allmydata.scripts import tahoe_status
    return tahoe_status.do_status(options)

dispatch = {
    "mkdir": mkdir,
    "add-alias": add_alias,
    "create-alias": create_alias,
    "list-aliases": list_aliases,
    "ls": list,
    "get": get,
    "put": put,
    "cp": cp,
    "unlink": unlink,
    "rm": rm,
    "mv": mv,
    "ln": ln,
    "backup": backup,
    "webopen": webopen,
    "manifest": manifest,
    "stats": stats,
    "check": check,
    "deep-check": deepcheck,
    "status": status,
    }
