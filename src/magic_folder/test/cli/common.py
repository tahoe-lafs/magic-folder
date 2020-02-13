
from six.moves import (
    StringIO as MixedIO,
)
from allmydata.util.encodingutil import unicode_to_argv
from allmydata.scripts import runner

from eliot import (
    Message,
    log_call,
)

from ..common_util import ReallyEqualMixin, run_cli

from ...scripts.magic_folder_cli import (
    MagicFolderCommand,
    do_magic_folder,
)

def parse_options(basedir, command, args):
    o = runner.Options()
    o.parseOptions(["--node-directory", basedir, command] + args)
    while hasattr(o, "subOptions"):
        o = o.subOptions
    return o

class CLITestMixin(ReallyEqualMixin):
    def do_cli(self, verb, *args, **kwargs):
        # client_num is used to execute client CLI commands on a specific
        # client.
        client_num = kwargs.get("client_num", 0)
        client_dir = unicode_to_argv(self.get_clientdir(i=client_num))
        nodeargs = [ "--node-directory", client_dir ]
        return run_cli(verb, nodeargs=nodeargs, *args, **kwargs)


@log_call(action_type=u"test:cli", include_args=["argv"])
def cli(node_directory, argv):
    options = MagicFolderCommand()
    options.stdout = MixedIO()
    options.stderr = MixedIO()
    options.parseOptions([
        b"--debug",
        b"--node-directory",
        node_directory.asBytesMode().path,
    ] + argv)
    result = do_magic_folder(options)
    Message.log(
        message_type=u"stdout",
        value=options.stdout.getvalue(),
    )
    Message.log(
        message_type=u"stderr",
        value=options.stderr.getvalue(),
    )
    if result != 0:
        raise Exception("Got result {} from magic-folder {}".format(result, argv))
    return options.stdout.getvalue()
