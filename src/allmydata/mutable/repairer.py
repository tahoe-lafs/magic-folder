
from zope.interface import implementer
from twisted.internet import defer
from allmydata.interfaces import IRepairResults, ICheckResults
from allmydata.mutable.publish import MutableData
from allmydata.mutable.common import MODE_REPAIR
from allmydata.mutable.servermap import ServerMap, ServermapUpdater

@implementer(IRepairResults)
class RepairResults(object):

    def __init__(self, smap):
        self.servermap = smap
    def set_successful(self, successful):
        self.successful = successful
    def get_successful(self):
        return self.successful
    def to_string(self):
        return ""

class RepairRequiresWritecapError(Exception):
    """Repair currently requires a writecap."""

class MustForceRepairError(Exception):
    pass

class Repairer(object):
    def __init__(self, node, check_results, storage_broker, history, monitor):
        self.node = node
        self.check_results = ICheckResults(check_results)
        assert check_results.get_storage_index() == node.get_storage_index()
        self._storage_broker = storage_broker
        self._history = history
        self._monitor = monitor

    def start(self, force=False):
        # download, then re-publish. If a server had a bad share, try to
        # replace it with a good one of the same shnum.

        # The normal repair operation should not be used to replace
        # application-specific merging of alternate versions: i.e if there
        # are multiple highest seqnums with different roothashes. In this
        # case, the application must use node.upload() (referencing the
        # servermap that indicates the multiple-heads condition), or
        # node.overwrite(). The repair() operation will refuse to run in
        # these conditions unless a force=True argument is provided. If
        # force=True is used, then the highest root hash will be reinforced.

        # Likewise, the presence of an unrecoverable latest version is an
        # unusual event, and should ideally be handled by retrying a couple
        # times (spaced out over hours or days) and hoping that new shares
        # will become available. If repair(force=True) is called, data will
        # be lost: a new seqnum will be generated with the same contents as
        # the most recent recoverable version, skipping over the lost
        # version. repair(force=False) will refuse to run in a situation like
        # this.

        # Repair is designed to fix the following injuries:
        #  missing shares: add new ones to get at least N distinct ones
        #  old shares: replace old shares with the latest version
        #  bogus shares (bad sigs): replace the bad one with a good one

        # first, update the servermap in MODE_REPAIR, which files all shares
        # and makes sure we get the privkey.
        u = ServermapUpdater(self.node, self._storage_broker, self._monitor,
                             ServerMap(), MODE_REPAIR)
        if self._history:
            self._history.notify_mapupdate(u.get_status())
        d = u.update()
        d.addCallback(self._got_full_servermap, force)
        return d

    def _got_full_servermap(self, smap, force):
        best_version = smap.best_recoverable_version()
        if not best_version:
            # the file is damaged beyond repair
            rr = RepairResults(smap)
            rr.set_successful(False)
            return defer.succeed(rr)

        if smap.unrecoverable_newer_versions():
            if not force:
                raise MustForceRepairError("There were unrecoverable newer "
                                           "versions, so force=True must be "
                                           "passed to the repair() operation")
            # continuing on means that node.upload() will pick a seqnum that
            # is higher than everything visible in the servermap, effectively
            # discarding the unrecoverable versions.
        if smap.needs_merge():
            if not force:
                raise MustForceRepairError("There were multiple recoverable "
                                           "versions with identical seqnums, "
                                           "so force=True must be passed to "
                                           "the repair() operation")
            # continuing on means that smap.best_recoverable_version() will
            # pick the one with the highest roothash, and then node.upload()
            # will replace all shares with its contents

        # missing shares are handled during upload, which tries to find a
        # home for every share

        # old shares are handled during upload, which will replace any share
        # that was present in the servermap

        # bogus shares need to be managed here. We might notice a bogus share
        # during mapupdate (whether done for a filecheck or just before a
        # download) by virtue of it having an invalid signature. We might
        # also notice a bad hash in the share during verify or download. In
        # either case, the problem will be noted in the servermap, and the
        # bad share (along with its checkstring) will be recorded in
        # servermap.bad_shares . Publish knows that it should try and replace
        # these.

        # I chose to use the retrieve phase to ensure that the privkey is
        # available, to avoid the extra roundtrip that would occur if we,
        # say, added an smap.get_privkey() method.

        if not self.node.get_writekey():
            raise RepairRequiresWritecapError("Sorry, repair currently requires a writecap, to set the write-enabler properly.")

        d = self.node.download_version(smap, best_version, fetch_privkey=True)
        d.addCallback(lambda data:
            MutableData(data))
        d.addCallback(self.node.upload, smap)
        d.addCallback(self.get_results, smap)
        return d

    def get_results(self, res, smap):
        rr = RepairResults(smap)
        rr.set_successful(True)
        return rr
