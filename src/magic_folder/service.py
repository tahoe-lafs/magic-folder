# Copyright (c) Least Authority TFA GmbH.
# See COPYING.* for details.

import json
from configparser import (
    ConfigParser,
)

import attr
from eliot import start_action
from eliot.twisted import inline_callbacks
from treq.client import HTTPClient
from twisted.application.service import (
    MultiService,
)
from twisted.internet.defer import Deferred, gatherResults, returnValue
from twisted.internet.endpoints import serverFromString
from twisted.web import http
from twisted.web.client import Agent
from twisted.python.compat import (
    nativeString,
)

from .common import APIError, NoSuchMagicFolder
from .endpoints import client_endpoint_from_address
from .magic_folder import MagicFolder
from .snapshot import create_local_author
from .status import (
    IStatus,
    WebSocketStatusService,
    TahoeStatus,
)
from .tahoe_client import (
    InsufficientStorageServers,
    create_tahoe_client,
)
from .util.observer import (
    ListenObserver,
)
from .util.twisted import (
    PeriodicService,
)
from .web import magic_folder_web_service


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
class ConnectedTahoeService(MultiService):
    """
    A service that periodically checks whether the Tahoe client we're
    using is currently connected to 'enough' servers.

    This is reflected in our status service.

    When creating Tahoe objects, it can be important to know whether
    we are currently connected to 'enough' servers (e.g. >=
    'happy'). This is especially important for mutables because they
    do not follow the 'servers of happiness' algorithm and will not
    fail if there are less than a 'happy' number of storage servers
    currently connected.

    While "check, then create mutable" still leaves a window when we
    _could_ create a mutable with fewer than 'happy' servers, it
    reduces the window considerably.

    Note: find_happy_shares() is 'lazy' because the test fixtures play a bit
    of a trick to avoid having to have a 'real' tahoe directory...
    """
    reactor = attr.ib()
    find_happy_shares = attr.ib()  # callable() -> int
    status_service = attr.ib(validator=attr.validators.provides(IStatus))
    tahoe_client = attr.ib()

    # internal state
    happy = attr.ib(default=None)
    _poller = attr.ib(default=None)
    _last_update = attr.ib(default=0)
    _storage_servers = attr.ib(factory=dict)

    def __attrs_post_init__(self):
        MultiService.__init__(self)

    def startService(self):
        MultiService.startService(self)
        self.happy = self.find_happy_shares()
        self._poller = PeriodicService(
            self.reactor,
            5,
            self._update_status,
        )
        self._poller.setServiceParent(self)

    @inline_callbacks
    def is_happy_connections(self):
        yield self._poller.call_soon()
        returnValue(self.connected_servers() >= self.happy)

    def connected_servers(self):
        """
        :returns int: the number of storage-servers our Tahoe-LAFS client
        is currently connected to.
        """
        return sum(
            1 if server["connection_status"].startswith("Connected to") else 0
            for server in self._storage_servers
        )

    @inline_callbacks
    def _update_status(self):
        try:
            welcome_body = yield self.tahoe_client.get_welcome()
            self._storage_servers = welcome_body["servers"]
            status = TahoeStatus(self.connected_servers(), self.happy, True)
        except Exception:
            self._storage_servers = {}
            status = TahoeStatus(0, self.happy, False)

        # update status
        self.status_service.tahoe_status(status)

        # tell TahoeClient whether mutable operation is fine or not
        if status.is_happy:
            self.tahoe_client.mutables_okay()
        else:
            self.tahoe_client.mutables_bad(
                InsufficientStorageServers(
                    status.connected,
                    status.desired,
                )
            )

from twisted.logger import (
    Logger,
)


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
    _run_deferred = attr.ib(init=False, factory=Deferred)
    _cooperator = attr.ib(default=None)
    log = Logger()

    def __attrs_post_init__(self):
        MultiService.__init__(self)
        if self.tahoe_client is None:
            self.tahoe_client = create_tahoe_client(
                self.config.tahoe_client_url,
                HTTPClient(Agent(self.reactor)),
            )
        self._listen_endpoint = ListenObserver(
            serverFromString(
                self.reactor,
                nativeString(self.config.api_endpoint),
            )
        )
        web_service = magic_folder_web_service(
            self._listen_endpoint,
            self.config,
            self,
            self._get_auth_token,
            self.status_service,
        )
        web_service.setServiceParent(self)

        def find_happy_shares():
            tahoe_config = read_tahoe_config(self.config.tahoe_node_directory)
            return int(tahoe_config.get("client", "shares.happy"))

        self._tahoe_status_service = ConnectedTahoeService(
            self.reactor,
            find_happy_shares,
            self.status_service,
            self.tahoe_client,
        )
        self._tahoe_status_service.setServiceParent(self)

        # We can create the services for all configured folders right now.
        # They won't do anything until they are started which won't happen
        # until this service is started.
        for name in self.config.list_magic_folders():
            self._add_service_for_folder(name)

    def _add_service_for_folder(self, name):
        """
        Create and attach the child service for the given magic folder.
        """
        mf = MagicFolder.from_config(
            self.reactor,
            self.tahoe_client,
            name,
            self.config,
            self.status_service,
            cooperator=self._cooperator,
        )
        mf.setServiceParent(self)
        return mf

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

        :raise NoSuchMagicFolder: If no magic-folder with a matching name is found.

        :return MagicFolder: The service for the matching magic-folder.
        """
        for service in self._iter_magic_folder_services():
            if service.folder_name == folder_name:
                return service
        raise NoSuchMagicFolder(folder_name)

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

    @inline_callbacks
    def run(self):
        yield self.startService()
        happy = yield self._tahoe_status_service.is_happy_connections()
        self.log.info(
            "Connected to {} storage-servers".format(
                self._tahoe_status_service.connected_servers()
            ),
        )
        if not happy:
            self.log.info(
                "NOTE: not currently connected to enough storage-servers",
            )

        def do_shutdown():
            self._run_deferred.callback(None)
            return self.stopService()
        self.reactor.addSystemEventTrigger("before", "shutdown", do_shutdown)

        yield self._run_deferred

    def startService(self):
        MultiService.startService(self)

        def _set_api_endpoint(port):
            endpoint = client_endpoint_from_address(port.getHost())
            if endpoint is not None:
                self.config.api_client_endpoint = endpoint
            return None

        def _stop_reactor(failure):
            self.config.api_client_endpoint = None
            self._run_deferred.errback(failure)
            return None

        observe = self._listen_endpoint.observe()
        observe.addCallback(_set_api_endpoint)
        observe.addErrback(_stop_reactor)
        ds = [observe]
        for magic_folder in self._iter_magic_folder_services():
            ds.append(magic_folder.ready())

        # double-check that our api-endpoint exists properly in the "output" file
        self.config._write_api_client_endpoint()

        # The integration tests look for this message.  You cannot get rid of
        # it (without also changing the tests).
        self.log.info(
            "Completed initial Magic Folder setup",
        )
        self._starting = gatherResults(ds)

    @inline_callbacks
    def stopService(self):
        try:
            self._starting.cancel()
            yield MultiService.stopService(self)
            yield self._starting
        finally:
            self.config.api_client_endpoint = None

    @inline_callbacks
    def create_folder(self, name, author_name, local_dir, poll_interval, scan_interval):
        """
        Create a magic-folder with the specified ``name`` and
        ``local_dir``.

        :param unicode name: The name of the magic-folder.

        :param unicode author_name: The name for our author

        :param FilePath local_dir: The directory on the filesystem that the user wants
            to sync between different computers.

        :param integer poll_interval: Periodic time interval after which the
            client polls for updates.

        :param integer scan_interval: Every 'scan_interval' seconds the
            local directory will be scanned for changes.

        :return Deferred: ``None`` or an appropriate exception is raised.
        """
        if name in self.config.list_magic_folders():
            raise APIError(
                code=http.CONFLICT,
                reason="Already have a magic-folder named '{}'".format(name),
            )

        if scan_interval is not None and scan_interval <= 0:
            raise APIError(
                code=http.BAD_REQUEST,
                reason="scan_interval must be positive integer or null",
            )

        # create our author
        author = create_local_author(author_name)

        # create '@metadata' content for our directory and collective;
        # note that we're re-using it here because it has the same
        # data but in principal the metadata could be different for
        # collective vs. personal DMDs -- also currently it fits into
        # a LIT cap anyway (so they'll be identical)
        dir_metadata_cap = yield self.tahoe_client.create_immutable(
            json.dumps({"version": 1}).encode("utf8")
        )

        # create an unlinked directory and get the collective write-cap
        collective_write_cap = yield self.tahoe_client.create_mutable_directory()
        yield self.tahoe_client.add_entry_to_mutable_directory(
            mutable_cap=collective_write_cap,
            path_name="@metadata",
            entry_cap=dir_metadata_cap,
        )

        # create the personal dmd write-cap
        personal_write_cap = yield self.tahoe_client.create_mutable_directory()
        yield self.tahoe_client.add_entry_to_mutable_directory(
            mutable_cap=personal_write_cap,
            path_name="@metadata",
            entry_cap=dir_metadata_cap,
        )

        # 'attenuate' our personal dmd write-cap to a read-cap
        personal_readonly_cap = personal_write_cap.to_readonly()

        # add ourselves to the collective
        yield self.tahoe_client.add_entry_to_mutable_directory(
            mutable_cap=collective_write_cap,
            path_name=author_name,
            entry_cap=personal_readonly_cap,
        )

        self.config.create_magic_folder(
            name,
            local_dir,
            author,
            collective_write_cap,
            personal_write_cap,
            poll_interval,
            scan_interval,
        )

        mf = self._add_service_for_folder(name)
        yield mf.ready()
        self.status_service._maybe_update_clients()

    @inline_callbacks
    def leave_folder(self, name, really_delete_write_capability):
        with start_action(
            action_type="service:leave-folder",
            name=name,
            really_delete_write_capability=really_delete_write_capability,
        ):
            folder = self.get_folder_service(name)

            if folder.config.is_admin():
                if not really_delete_write_capability:
                    raise APIError(
                        code=http.CONFLICT,
                        reason="magic folder '{}' holds a write capability"
                        ", not deleting.".format(name),
                    )

            yield folder.disownServiceParent()
            self.status_service.folder_gone(name)
            fails = self.config.remove_magic_folder(name)
            if fails:
                raise APIError(
                    code=http.INTERNAL_SERVER_ERROR,
                    reason="Problems while removing state directories:",
                    extra_fields={
                        "details": {path: str(error) for (path, error) in fails}
                    },
                )
