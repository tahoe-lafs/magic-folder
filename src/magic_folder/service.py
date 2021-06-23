# Copyright (c) Least Authority TFA GmbH.
# See COPYING.* for details.

from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

from configparser import ConfigParser

import attr
from eliot.twisted import inline_callbacks
from treq.client import HTTPClient
from twisted.application.service import MultiService
from twisted.internet.defer import Deferred, gatherResults, returnValue
from twisted.internet.endpoints import serverFromString
from twisted.internet.task import deferLater
from twisted.web.client import Agent

from .magic_folder import MagicFolder
from .status import IStatus, WebSocketStatusService
from .tahoe_client import create_tahoe_client
from .web import magic_folder_web_service


@inline_callbacks
def poll(label, operation, reactor):
    while True:
        print("Polling {}...".format(label))
        status, message = yield operation()
        if status:
            print("{}: {}, done.".format(label, message))
            break
        print("Not {}: {}".format(label, message))
        yield deferLater(reactor, 1.0, lambda: None)


def read_tahoe_config(node_directory):
    """
    :return ConfigParser: The parsed configuration file.
    """
    config = node_directory.child("tahoe.cfg").getContent()
    # Byte Order Mark is an optional garbage code point you sometimes get at
    # the start of UTF-8 encoded files. Especially on Windows. Skip it by using
    # utf-8-sig. https://en.wikipedia.org/wiki/Byte_order_mark
    parser = ConfigParser(strict=False)
    parser.read_string(config.decode("utf-8-sig"))
    return parser


@attr.s
class MagicFolderService(MultiService):
    """
    :ivar reactor: the Twisted reactor to use

    :ivar GlobalConfigDatabase config: our system configuration
    """
    reactor = attr.ib()
    config = attr.ib()
    status_service = attr.ib(validator=attr.validators.provides(IStatus))
    tahoe_client = attr.ib(default=None)

    def __attrs_post_init__(self):
        MultiService.__init__(self)
        if self.tahoe_client is None:
            self.tahoe_client = create_tahoe_client(
                self.config.tahoe_client_url,
                HTTPClient(Agent(self.reactor)),
            )
        self._listen_endpoint = serverFromString(
            self.reactor,
            self.config.api_endpoint,
        )
        web_service = magic_folder_web_service(
            self._listen_endpoint,
            self.config,
            self,
            self._get_auth_token,
            self.tahoe_client,
            self.status_service,
        )
        web_service.setServiceParent(self)

        # We can create the services for all configured folders right now.
        # They won't do anything until they are started which won't happen
        # until this service is started.
        self._create_magic_folder_services()

    def _create_magic_folder_services(self):
        """
        Create all of the child magic folder services and attach them to this
        service.
        """
        for name in self.config.list_magic_folders():
            mf = MagicFolder.from_config(
                self.reactor,
                self.tahoe_client,
                name,
                self.config,
                self.status_service,
            )
            mf.setServiceParent(self)

    def _iter_magic_folder_services(self):
        """
        Iterate over all of the magic folder services which are children of this
        service.
        """
        for service in self:
            if isinstance(service, MagicFolder):
                yield service

    def get_folder_service(self, folder_name):
        """
        Look up a ``MagicFolder`` by its name.

        :param unicode folder_name: The name of the magic-folder to retrieve.

        :raise KeyError: If no magic-folder with a matching name is found.

        :return MagicFolder: The service for the matching magic-folder.
        """
        for service in self._iter_magic_folder_services():
            if service.folder_name == folder_name:
                return service
        raise KeyError(folder_name)

    def _get_auth_token(self):
        return self.config.api_token

    @classmethod
    def from_config(cls, reactor, config):
        """
        Create a new service given a reactor and global configuration.

        :param GlobalConfigDatabase config: config to use
        """
        return cls(
            reactor,
            config,
            WebSocketStatusService(reactor, config),
        )

    def _when_connected_enough(self):
        # start processing the upload queue when we've connected to
        # enough servers
        tahoe_config = read_tahoe_config(self.config.tahoe_node_directory)
        threshold = int(tahoe_config.get("client", "shares.needed"))

        @inline_callbacks
        def enough():
            try:
                welcome_body = yield self.tahoe_client.get_welcome()
            except Exception:
                returnValue((False, "Failed to get welcome page"))

            servers = welcome_body[u"servers"]
            connected_servers = [
                server
                for server in servers
                if server["connection_status"].startswith("Connected ")
            ]

            message = "Found {} of {} connected servers (want {})".format(
                len(connected_servers),
                len(servers),
                threshold,
            )

            if len(connected_servers) < threshold:
                returnValue((False, message))
            returnValue((True, message))
        return poll("connected enough", enough, self.reactor)

    def run(self):
        d = self._when_connected_enough()
        d.addCallback(lambda ignored: self.startService())
        d.addCallback(lambda ignored: Deferred())
        return d

    def startService(self):
        MultiService.startService(self)

        ds = []
        for magic_folder in self._iter_magic_folder_services():
            ds.append(magic_folder.ready())
        # The integration tests look for this message.  You cannot get rid of
        # it (without also changing the tests).
        print("Completed initial Magic Folder setup")
        self._starting = gatherResults(ds)

    def stopService(self):
        self._starting.cancel()
        MultiService.stopService(self)
        return self._starting
