import os, re, weakref, struct, time
import six

from foolscap.api import Referenceable
from twisted.application import service

from zope.interface import implementer
from allmydata.interfaces import RIStorageServer, IStatsProducer
from allmydata.util import fileutil, idlib, log, time_format
import allmydata # for __full_version__

from allmydata.storage.common import si_b2a, si_a2b, storage_index_to_dir
_pyflakes_hush = [si_b2a, si_a2b, storage_index_to_dir] # re-exported
from allmydata.storage.lease import LeaseInfo
from allmydata.storage.mutable import MutableShareFile, EmptyShare, \
     create_mutable_sharefile
from allmydata.mutable.layout import MAX_MUTABLE_SHARE_SIZE
from allmydata.storage.immutable import ShareFile, BucketWriter, BucketReader
from allmydata.storage.crawler import BucketCountingCrawler
from allmydata.storage.expirer import LeaseCheckingCrawler

# storage/
# storage/shares/incoming
#   incoming/ holds temp dirs named $START/$STORAGEINDEX/$SHARENUM which will
#   be moved to storage/shares/$START/$STORAGEINDEX/$SHARENUM upon success
# storage/shares/$START/$STORAGEINDEX
# storage/shares/$START/$STORAGEINDEX/$SHARENUM

# Where "$START" denotes the first 10 bits worth of $STORAGEINDEX (that's 2
# base-32 chars).

# $SHARENUM matches this regex:
NUM_RE=re.compile("^[0-9]+$")



@implementer(RIStorageServer, IStatsProducer)
class StorageServer(service.MultiService, Referenceable):
    name = 'storage'
    LeaseCheckerClass = LeaseCheckingCrawler

    def __init__(self, storedir, nodeid, reserved_space=0,
                 discard_storage=False, readonly_storage=False,
                 stats_provider=None,
                 expiration_enabled=False,
                 expiration_mode="age",
                 expiration_override_lease_duration=None,
                 expiration_cutoff_date=None,
                 expiration_sharetypes=("mutable", "immutable")):
        service.MultiService.__init__(self)
        assert isinstance(nodeid, str)
        assert len(nodeid) == 20
        self.my_nodeid = nodeid
        self.storedir = storedir
        sharedir = os.path.join(storedir, "shares")
        fileutil.make_dirs(sharedir)
        self.sharedir = sharedir
        # we don't actually create the corruption-advisory dir until necessary
        self.corruption_advisory_dir = os.path.join(storedir,
                                                    "corruption-advisories")
        self.reserved_space = int(reserved_space)
        self.no_storage = discard_storage
        self.readonly_storage = readonly_storage
        self.stats_provider = stats_provider
        if self.stats_provider:
            self.stats_provider.register_producer(self)
        self.incomingdir = os.path.join(sharedir, 'incoming')
        self._clean_incomplete()
        fileutil.make_dirs(self.incomingdir)
        self._active_writers = weakref.WeakKeyDictionary()
        log.msg("StorageServer created", facility="tahoe.storage")

        if reserved_space:
            if self.get_available_space() is None:
                log.msg("warning: [storage]reserved_space= is set, but this platform does not support an API to get disk statistics (statvfs(2) or GetDiskFreeSpaceEx), so this reservation cannot be honored",
                        umin="0wZ27w", level=log.UNUSUAL)

        self.latencies = {"allocate": [], # immutable
                          "write": [],
                          "close": [],
                          "read": [],
                          "get": [],
                          "writev": [], # mutable
                          "readv": [],
                          "add-lease": [], # both
                          "renew": [],
                          "cancel": [],
                          }
        self.add_bucket_counter()

        statefile = os.path.join(self.storedir, "lease_checker.state")
        historyfile = os.path.join(self.storedir, "lease_checker.history")
        klass = self.LeaseCheckerClass
        self.lease_checker = klass(self, statefile, historyfile,
                                   expiration_enabled, expiration_mode,
                                   expiration_override_lease_duration,
                                   expiration_cutoff_date,
                                   expiration_sharetypes)
        self.lease_checker.setServiceParent(self)

    def __repr__(self):
        return "<StorageServer %s>" % (idlib.shortnodeid_b2a(self.my_nodeid),)

    def have_shares(self):
        # quick test to decide if we need to commit to an implicit
        # permutation-seed or if we should use a new one
        return bool(set(os.listdir(self.sharedir)) - set(["incoming"]))

    def add_bucket_counter(self):
        statefile = os.path.join(self.storedir, "bucket_counter.state")
        self.bucket_counter = BucketCountingCrawler(self, statefile)
        self.bucket_counter.setServiceParent(self)

    def count(self, name, delta=1):
        if self.stats_provider:
            self.stats_provider.count("storage_server." + name, delta)

    def add_latency(self, category, latency):
        a = self.latencies[category]
        a.append(latency)
        if len(a) > 1000:
            self.latencies[category] = a[-1000:]

    def get_latencies(self):
        """Return a dict, indexed by category, that contains a dict of
        latency numbers for each category. If there are sufficient samples
        for unambiguous interpretation, each dict will contain the
        following keys: mean, 01_0_percentile, 10_0_percentile,
        50_0_percentile (median), 90_0_percentile, 95_0_percentile,
        99_0_percentile, 99_9_percentile.  If there are insufficient
        samples for a given percentile to be interpreted unambiguously
        that percentile will be reported as None. If no samples have been
        collected for the given category, then that category name will
        not be present in the return value. """
        # note that Amazon's Dynamo paper says they use 99.9% percentile.
        output = {}
        for category in self.latencies:
            if not self.latencies[category]:
                continue
            stats = {}
            samples = self.latencies[category][:]
            count = len(samples)
            stats["samplesize"] = count
            samples.sort()
            if count > 1:
                stats["mean"] = sum(samples) / count
            else:
                stats["mean"] = None

            orderstatlist = [(0.01, "01_0_percentile", 100), (0.1, "10_0_percentile", 10),\
                             (0.50, "50_0_percentile", 10), (0.90, "90_0_percentile", 10),\
                             (0.95, "95_0_percentile", 20), (0.99, "99_0_percentile", 100),\
                             (0.999, "99_9_percentile", 1000)]

            for percentile, percentilestring, minnumtoobserve in orderstatlist:
                if count >= minnumtoobserve:
                    stats[percentilestring] = samples[int(percentile*count)]
                else:
                    stats[percentilestring] = None

            output[category] = stats
        return output

    def log(self, *args, **kwargs):
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.storage"
        return log.msg(*args, **kwargs)

    def _clean_incomplete(self):
        fileutil.rm_dir(self.incomingdir)

    def get_stats(self):
        # remember: RIStatsProvider requires that our return dict
        # contains numeric values.
        stats = { 'storage_server.allocated': self.allocated_size(), }
        stats['storage_server.reserved_space'] = self.reserved_space
        for category,ld in self.get_latencies().items():
            for name,v in ld.items():
                stats['storage_server.latencies.%s.%s' % (category, name)] = v

        try:
            disk = fileutil.get_disk_stats(self.sharedir, self.reserved_space)
            writeable = disk['avail'] > 0

            # spacetime predictors should use disk_avail / (d(disk_used)/dt)
            stats['storage_server.disk_total'] = disk['total']
            stats['storage_server.disk_used'] = disk['used']
            stats['storage_server.disk_free_for_root'] = disk['free_for_root']
            stats['storage_server.disk_free_for_nonroot'] = disk['free_for_nonroot']
            stats['storage_server.disk_avail'] = disk['avail']
        except AttributeError:
            writeable = True
        except EnvironmentError:
            log.msg("OS call to get disk statistics failed", level=log.UNUSUAL)
            writeable = False

        if self.readonly_storage:
            stats['storage_server.disk_avail'] = 0
            writeable = False

        stats['storage_server.accepting_immutable_shares'] = int(writeable)
        s = self.bucket_counter.get_state()
        bucket_count = s.get("last-complete-bucket-count")
        if bucket_count:
            stats['storage_server.total_bucket_count'] = bucket_count
        return stats

    def get_available_space(self):
        """Returns available space for share storage in bytes, or None if no
        API to get this information is available."""

        if self.readonly_storage:
            return 0
        return fileutil.get_available_space(self.sharedir, self.reserved_space)

    def allocated_size(self):
        space = 0
        for bw in self._active_writers:
            space += bw.allocated_size()
        return space

    def remote_get_version(self):
        remaining_space = self.get_available_space()
        if remaining_space is None:
            # We're on a platform that has no API to get disk stats.
            remaining_space = 2**64

        version = { "http://allmydata.org/tahoe/protocols/storage/v1" :
                    { "maximum-immutable-share-size": remaining_space,
                      "maximum-mutable-share-size": MAX_MUTABLE_SHARE_SIZE,
                      "available-space": remaining_space,
                      "tolerates-immutable-read-overrun": True,
                      "delete-mutable-shares-with-zero-length-writev": True,
                      "fills-holes-with-zero-bytes": True,
                      "prevents-read-past-end-of-share-data": True,
                      },
                    "application-version": str(allmydata.__full_version__),
                    }
        return version

    def remote_allocate_buckets(self, storage_index,
                                renew_secret, cancel_secret,
                                sharenums, allocated_size,
                                canary, owner_num=0):
        # owner_num is not for clients to set, but rather it should be
        # curried into the PersonalStorageServer instance that is dedicated
        # to a particular owner.
        start = time.time()
        self.count("allocate")
        alreadygot = set()
        bucketwriters = {} # k: shnum, v: BucketWriter
        si_dir = storage_index_to_dir(storage_index)
        si_s = si_b2a(storage_index)

        log.msg("storage: allocate_buckets %s" % si_s)

        # in this implementation, the lease information (including secrets)
        # goes into the share files themselves. It could also be put into a
        # separate database. Note that the lease should not be added until
        # the BucketWriter has been closed.
        expire_time = time.time() + 31*24*60*60
        lease_info = LeaseInfo(owner_num,
                               renew_secret, cancel_secret,
                               expire_time, self.my_nodeid)

        max_space_per_bucket = allocated_size

        remaining_space = self.get_available_space()
        limited = remaining_space is not None
        if limited:
            # this is a bit conservative, since some of this allocated_size()
            # has already been written to disk, where it will show up in
            # get_available_space.
            remaining_space -= self.allocated_size()
        # self.readonly_storage causes remaining_space <= 0

        # fill alreadygot with all shares that we have, not just the ones
        # they asked about: this will save them a lot of work. Add or update
        # leases for all of them: if they want us to hold shares for this
        # file, they'll want us to hold leases for this file.
        for (shnum, fn) in self._get_bucket_shares(storage_index):
            alreadygot.add(shnum)
            sf = ShareFile(fn)
            sf.add_or_renew_lease(lease_info)

        for shnum in sharenums:
            incominghome = os.path.join(self.incomingdir, si_dir, "%d" % shnum)
            finalhome = os.path.join(self.sharedir, si_dir, "%d" % shnum)
            if os.path.exists(finalhome):
                # great! we already have it. easy.
                pass
            elif os.path.exists(incominghome):
                # Note that we don't create BucketWriters for shnums that
                # have a partial share (in incoming/), so if a second upload
                # occurs while the first is still in progress, the second
                # uploader will use different storage servers.
                pass
            elif (not limited) or (remaining_space >= max_space_per_bucket):
                # ok! we need to create the new share file.
                bw = BucketWriter(self, incominghome, finalhome,
                                  max_space_per_bucket, lease_info, canary)
                if self.no_storage:
                    bw.throw_out_all_data = True
                bucketwriters[shnum] = bw
                self._active_writers[bw] = 1
                if limited:
                    remaining_space -= max_space_per_bucket
            else:
                # bummer! not enough space to accept this bucket
                pass

        if bucketwriters:
            fileutil.make_dirs(os.path.join(self.sharedir, si_dir))

        self.add_latency("allocate", time.time() - start)
        return alreadygot, bucketwriters

    def _iter_share_files(self, storage_index):
        for shnum, filename in self._get_bucket_shares(storage_index):
            f = open(filename, 'rb')
            header = f.read(32)
            f.close()
            if header[:32] == MutableShareFile.MAGIC:
                sf = MutableShareFile(filename, self)
                # note: if the share has been migrated, the renew_lease()
                # call will throw an exception, with information to help the
                # client update the lease.
            elif header[:4] == struct.pack(">L", 1):
                sf = ShareFile(filename)
            else:
                continue # non-sharefile
            yield sf

    def remote_add_lease(self, storage_index, renew_secret, cancel_secret,
                         owner_num=1):
        start = time.time()
        self.count("add-lease")
        new_expire_time = time.time() + 31*24*60*60
        lease_info = LeaseInfo(owner_num,
                               renew_secret, cancel_secret,
                               new_expire_time, self.my_nodeid)
        for sf in self._iter_share_files(storage_index):
            sf.add_or_renew_lease(lease_info)
        self.add_latency("add-lease", time.time() - start)
        return None

    def remote_renew_lease(self, storage_index, renew_secret):
        start = time.time()
        self.count("renew")
        new_expire_time = time.time() + 31*24*60*60
        found_buckets = False
        for sf in self._iter_share_files(storage_index):
            found_buckets = True
            sf.renew_lease(renew_secret, new_expire_time)
        self.add_latency("renew", time.time() - start)
        if not found_buckets:
            raise IndexError("no such lease to renew")

    def bucket_writer_closed(self, bw, consumed_size):
        if self.stats_provider:
            self.stats_provider.count('storage_server.bytes_added', consumed_size)
        del self._active_writers[bw]

    def _get_bucket_shares(self, storage_index):
        """Return a list of (shnum, pathname) tuples for files that hold
        shares for this storage_index. In each tuple, 'shnum' will always be
        the integer form of the last component of 'pathname'."""
        storagedir = os.path.join(self.sharedir, storage_index_to_dir(storage_index))
        try:
            for f in os.listdir(storagedir):
                if NUM_RE.match(f):
                    filename = os.path.join(storagedir, f)
                    yield (int(f), filename)
        except OSError:
            # Commonly caused by there being no buckets at all.
            pass

    def remote_get_buckets(self, storage_index):
        start = time.time()
        self.count("get")
        si_s = si_b2a(storage_index)
        log.msg("storage: get_buckets %s" % si_s)
        bucketreaders = {} # k: sharenum, v: BucketReader
        for shnum, filename in self._get_bucket_shares(storage_index):
            bucketreaders[shnum] = BucketReader(self, filename,
                                                storage_index, shnum)
        self.add_latency("get", time.time() - start)
        return bucketreaders

    def get_leases(self, storage_index):
        """Provide an iterator that yields all of the leases attached to this
        bucket. Each lease is returned as a LeaseInfo instance.

        This method is not for client use.

        :note: Only for immutable shares.
        """
        # since all shares get the same lease data, we just grab the leases
        # from the first share
        try:
            shnum, filename = self._get_bucket_shares(storage_index).next()
            sf = ShareFile(filename)
            return sf.get_leases()
        except StopIteration:
            return iter([])

    def get_slot_leases(self, storage_index):
        """
        This method is not for client use.

        :note: Only for mutable shares.

        :return: An iterable of the leases attached to this slot.
        """
        for _, share_filename in self._get_bucket_shares(storage_index):
            share = MutableShareFile(share_filename)
            return share.get_leases()
        return []

    def _collect_mutable_shares_for_storage_index(self, bucketdir, write_enabler, si_s):
        """
        Gather up existing mutable shares for the given storage index.

        :param bytes bucketdir: The filesystem path containing shares for the
            given storage index.

        :param bytes write_enabler: The write enabler secret for the shares.

        :param bytes si_s: The storage index in encoded (base32) form.

        :raise BadWriteEnablerError: If the write enabler is not correct for
            any of the collected shares.

        :return dict[int, MutableShareFile]: The collected shares in a mapping
            from integer share numbers to ``MutableShareFile`` instances.
        """
        shares = {}
        if os.path.isdir(bucketdir):
            # shares exist if there is a file for them
            for sharenum_s in os.listdir(bucketdir):
                try:
                    sharenum = int(sharenum_s)
                except ValueError:
                    continue
                filename = os.path.join(bucketdir, sharenum_s)
                msf = MutableShareFile(filename, self)
                msf.check_write_enabler(write_enabler, si_s)
                shares[sharenum] = msf
        return shares

    def _evaluate_test_vectors(self, test_and_write_vectors, shares):
        """
        Execute test vectors against share data.

        :param test_and_write_vectors: See
            ``allmydata.interfaces.TestAndWriteVectorsForShares``.

        :param dict[int, MutableShareFile] shares: The shares against which to
            execute the vectors.

        :return bool: ``True`` if and only if all of the test vectors succeed
            against the given shares.
        """
        for sharenum in test_and_write_vectors:
            (testv, datav, new_length) = test_and_write_vectors[sharenum]
            if sharenum in shares:
                if not shares[sharenum].check_testv(testv):
                    self.log("testv failed: [%d]: %r" % (sharenum, testv))
                    return False
            else:
                # compare the vectors against an empty share, in which all
                # reads return empty strings.
                if not EmptyShare().check_testv(testv):
                    self.log("testv failed (empty): [%d] %r" % (sharenum,
                                                                testv))
                    return False
        return True

    def _evaluate_read_vectors(self, read_vector, shares):
        """
        Execute read vectors against share data.

        :param read_vector: See ``allmydata.interfaces.ReadVector``.

        :param dict[int, MutableShareFile] shares: The shares against which to
            execute the vector.

        :return dict[int, bytes]: The data read from the shares.
        """
        read_data = {}
        for sharenum, share in shares.items():
            read_data[sharenum] = share.readv(read_vector)
        return read_data

    def _evaluate_write_vectors(self, bucketdir, secrets, test_and_write_vectors, shares):
        """
        Execute write vectors against share data.

        :param bytes bucketdir: The parent directory holding the shares.  This
            is removed if the last share is removed from it.  If shares are
            created, they are created in it.

        :param secrets: A tuple of ``WriteEnablerSecret``,
            ``LeaseRenewSecret``, and ``LeaseCancelSecret``.  These secrets
            are used to initialize new shares.

        :param test_and_write_vectors: See
            ``allmydata.interfaces.TestAndWriteVectorsForShares``.

        :param dict[int, MutableShareFile]: The shares against which to
            execute the vectors.

        :return dict[int, MutableShareFile]: The shares which still exist
            after applying the vectors.
        """
        remaining_shares = {}

        for sharenum in test_and_write_vectors:
            (testv, datav, new_length) = test_and_write_vectors[sharenum]
            if new_length == 0:
                if sharenum in shares:
                    shares[sharenum].unlink()
            else:
                if sharenum not in shares:
                    # allocate a new share
                    allocated_size = 2000 # arbitrary, really
                    share = self._allocate_slot_share(bucketdir, secrets,
                                                      sharenum,
                                                      allocated_size,
                                                      owner_num=0)
                    shares[sharenum] = share
                shares[sharenum].writev(datav, new_length)
                remaining_shares[sharenum] = shares[sharenum]

            if new_length == 0:
                # delete bucket directories that exist but are empty.  They
                # might not exist if a client showed up and asked us to
                # truncate a share we weren't even holding.
                if os.path.exists(bucketdir) and [] == os.listdir(bucketdir):
                    os.rmdir(bucketdir)
        return remaining_shares

    def _make_lease_info(self, renew_secret, cancel_secret):
        """
        :return LeaseInfo: Information for a new lease for a share.
        """
        ownerid = 1 # TODO
        expire_time = time.time() + 31*24*60*60   # one month
        lease_info = LeaseInfo(ownerid,
                               renew_secret, cancel_secret,
                               expire_time, self.my_nodeid)
        return lease_info

    def _add_or_renew_leases(self, shares, lease_info):
        """
        Put the given lease onto the given shares.

        :param dict[int, MutableShareFile] shares: The shares to put the lease
            onto.

        :param LeaseInfo lease_info: The lease to put on the shares.
        """
        for share in six.viewvalues(shares):
            share.add_or_renew_lease(lease_info)

    def slot_testv_and_readv_and_writev(
            self,
            storage_index,
            secrets,
            test_and_write_vectors,
            read_vector,
            renew_leases,
    ):
        """
        Read data from shares and conditionally write some data to them.

        :param bool renew_leases: If and only if this is ``True`` and the test
            vectors pass then shares in this slot will also have an updated
            lease applied to them.

        See ``allmydata.interfaces.RIStorageServer`` for details about other
        parameters and return value.
        """
        start = time.time()
        self.count("writev")
        si_s = si_b2a(storage_index)
        log.msg("storage: slot_writev %s" % si_s)
        si_dir = storage_index_to_dir(storage_index)
        (write_enabler, renew_secret, cancel_secret) = secrets
        bucketdir = os.path.join(self.sharedir, si_dir)

        # If collection succeeds we know the write_enabler is good for all
        # existing shares.
        shares = self._collect_mutable_shares_for_storage_index(
            bucketdir,
            write_enabler,
            si_s,
        )

        # Now evaluate test vectors.
        testv_is_good = self._evaluate_test_vectors(
            test_and_write_vectors,
            shares,
        )

        # now gather the read vectors, before we do any writes
        read_data = self._evaluate_read_vectors(
            read_vector,
            shares,
        )

        if testv_is_good:
            # now apply the write vectors
            remaining_shares = self._evaluate_write_vectors(
                bucketdir,
                secrets,
                test_and_write_vectors,
                shares,
            )
            if renew_leases:
                lease_info = self._make_lease_info(renew_secret, cancel_secret)
                self._add_or_renew_leases(remaining_shares, lease_info)

        # all done
        self.add_latency("writev", time.time() - start)
        return (testv_is_good, read_data)

    def remote_slot_testv_and_readv_and_writev(self, storage_index,
                                               secrets,
                                               test_and_write_vectors,
                                               read_vector):
        return self.slot_testv_and_readv_and_writev(
            storage_index,
            secrets,
            test_and_write_vectors,
            read_vector,
            renew_leases=True,
        )

    def _allocate_slot_share(self, bucketdir, secrets, sharenum,
                             allocated_size, owner_num=0):
        (write_enabler, renew_secret, cancel_secret) = secrets
        my_nodeid = self.my_nodeid
        fileutil.make_dirs(bucketdir)
        filename = os.path.join(bucketdir, "%d" % sharenum)
        share = create_mutable_sharefile(filename, my_nodeid, write_enabler,
                                         self)
        return share

    def remote_slot_readv(self, storage_index, shares, readv):
        start = time.time()
        self.count("readv")
        si_s = si_b2a(storage_index)
        lp = log.msg("storage: slot_readv %s %s" % (si_s, shares),
                     facility="tahoe.storage", level=log.OPERATIONAL)
        si_dir = storage_index_to_dir(storage_index)
        # shares exist if there is a file for them
        bucketdir = os.path.join(self.sharedir, si_dir)
        if not os.path.isdir(bucketdir):
            self.add_latency("readv", time.time() - start)
            return {}
        datavs = {}
        for sharenum_s in os.listdir(bucketdir):
            try:
                sharenum = int(sharenum_s)
            except ValueError:
                continue
            if sharenum in shares or not shares:
                filename = os.path.join(bucketdir, sharenum_s)
                msf = MutableShareFile(filename, self)
                datavs[sharenum] = msf.readv(readv)
        log.msg("returning shares %s" % (datavs.keys(),),
                facility="tahoe.storage", level=log.NOISY, parent=lp)
        self.add_latency("readv", time.time() - start)
        return datavs

    def remote_advise_corrupt_share(self, share_type, storage_index, shnum,
                                    reason):
        fileutil.make_dirs(self.corruption_advisory_dir)
        now = time_format.iso_utc(sep="T")
        si_s = si_b2a(storage_index)
        # windows can't handle colons in the filename
        fn = os.path.join(self.corruption_advisory_dir,
                          "%s--%s-%d" % (now, si_s, shnum)).replace(":","")
        f = open(fn, "w")
        f.write("report: Share Corruption\n")
        f.write("type: %s\n" % share_type)
        f.write("storage_index: %s\n" % si_s)
        f.write("share_number: %d\n" % shnum)
        f.write("\n")
        f.write(reason)
        f.write("\n")
        f.close()
        log.msg(format=("client claims corruption in (%(share_type)s) " +
                        "%(si)s-%(shnum)d: %(reason)s"),
                share_type=share_type, si=si_s, shnum=shnum, reason=reason,
                level=log.SCARY, umid="SGx2fA")
        return None
