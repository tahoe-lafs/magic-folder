
import os, time, tempfile
from zope.interface import implementer
from twisted.application import service
from twisted.internet import defer
from twisted.internet.interfaces import IConsumer
from foolscap.api import Referenceable
from allmydata.interfaces import RIControlClient, IFileNode
from allmydata.util import fileutil, mathutil
from allmydata.immutable import upload
from allmydata.mutable.publish import MutableData
from twisted.python import log

def get_memory_usage():
    # this is obviously linux-specific
    stat_names = ("VmPeak",
                  "VmSize",
                  #"VmHWM",
                  "VmData")
    stats = {}
    try:
        for line in open("/proc/self/status", "r").readlines():
            name, right = line.split(":",2)
            if name in stat_names:
                assert right.endswith(" kB\n")
                right = right[:-4]
                stats[name] = int(right) * 1024
    except:
        # Probably not on (a compatible version of) Linux
        stats['VmSize'] = 0
        stats['VmPeak'] = 0
    return stats

def log_memory_usage(where=""):
    stats = get_memory_usage()
    log.msg("VmSize: %9d  VmPeak: %9d  %s" % (stats["VmSize"],
                                              stats["VmPeak"],
                                              where))

@implementer(IConsumer)
class FileWritingConsumer(object):
    def __init__(self, filename):
        self.done = False
        self.f = open(filename, "wb")
    def registerProducer(self, p, streaming):
        if streaming:
            p.resumeProducing()
        else:
            while not self.done:
                p.resumeProducing()
    def write(self, data):
        self.f.write(data)
    def unregisterProducer(self):
        self.done = True
        self.f.close()

@implementer(RIControlClient)
class ControlServer(Referenceable, service.Service):

    def remote_wait_for_client_connections(self, num_clients):
        return self.parent.debug_wait_for_client_connections(num_clients)

    def remote_upload_random_data_from_file(self, size, convergence):
        tempdir = tempfile.mkdtemp()
        filename = os.path.join(tempdir, "data")
        f = open(filename, "wb")
        block = "a" * 8192
        while size > 0:
            l = min(size, 8192)
            f.write(block[:l])
            size -= l
        f.close()
        uploader = self.parent.getServiceNamed("uploader")
        u = upload.FileName(filename, convergence=convergence)
        # XXX should pass reactor arg
        d = uploader.upload(u)
        d.addCallback(lambda results: results.get_uri())
        def _done(uri):
            os.remove(filename)
            os.rmdir(tempdir)
            return uri
        d.addCallback(_done)
        return d

    def remote_download_to_tempfile_and_delete(self, uri):
        tempdir = tempfile.mkdtemp()
        filename = os.path.join(tempdir, "data")
        filenode = self.parent.create_node_from_uri(uri, name=filename)
        if not IFileNode.providedBy(filenode):
            raise AssertionError("The URI does not reference a file.")
        c = FileWritingConsumer(filename)
        d = filenode.read(c)
        def _done(res):
            os.remove(filename)
            os.rmdir(tempdir)
            return None
        d.addCallback(_done)
        return d

    def remote_speed_test(self, count, size, mutable):
        assert size > 8
        log.msg("speed_test: count=%d, size=%d, mutable=%s" % (count, size,
                                                               mutable))
        st = SpeedTest(self.parent, count, size, mutable)
        return st.run()

    def remote_get_memory_usage(self):
        return get_memory_usage()

    def remote_measure_peer_response_time(self):
        # I'd like to average together several pings, but I don't want this
        # phase to take more than 10 seconds. Expect worst-case latency to be
        # 300ms.
        results = {}
        sb = self.parent.get_storage_broker()
        everyone = sb.get_connected_servers()
        num_pings = int(mathutil.div_ceil(10, (len(everyone) * 0.3)))
        everyone = list(everyone) * num_pings
        d = self._do_one_ping(None, everyone, results)
        return d
    def _do_one_ping(self, res, everyone_left, results):
        if not everyone_left:
            return results
        server = everyone_left.pop(0)
        server_name = server.get_longname()
        storage_server = server.get_storage_server()
        start = time.time()
        d = storage_server.get_buckets("\x00" * 16)
        def _done(ignored):
            stop = time.time()
            elapsed = stop - start
            if server_name in results:
                results[server_name].append(elapsed)
            else:
                results[server_name] = [elapsed]
        d.addCallback(_done)
        d.addCallback(self._do_one_ping, everyone_left, results)
        def _average(res):
            averaged = {}
            for server_name,times in results.iteritems():
                averaged[server_name] = sum(times) / len(times)
            return averaged
        d.addCallback(_average)
        return d

class SpeedTest(object):
    def __init__(self, parent, count, size, mutable):
        self.parent = parent
        self.count = count
        self.size = size
        self.mutable_mode = mutable
        self.uris = {}
        self.basedir = self.parent.config.get_config_path("_speed_test_data")

    def run(self):
        self.create_data()
        d = self.do_upload()
        d.addCallback(lambda res: self.do_download())
        d.addBoth(self.do_cleanup)
        d.addCallback(lambda res: (self.upload_time, self.download_time))
        return d

    def create_data(self):
        fileutil.make_dirs(self.basedir)
        for i in range(self.count):
            s = self.size
            fn = os.path.join(self.basedir, str(i))
            if os.path.exists(fn):
                os.unlink(fn)
            f = open(fn, "w")
            f.write(os.urandom(8))
            s -= 8
            while s > 0:
                chunk = min(s, 4096)
                f.write("\x00" * chunk)
                s -= chunk
            f.close()

    def do_upload(self):
        d = defer.succeed(None)
        def _create_slot(res):
            d1 = self.parent.create_mutable_file("")
            def _created(n):
                self._n = n
            d1.addCallback(_created)
            return d1
        if self.mutable_mode == "upload":
            d.addCallback(_create_slot)
        def _start(res):
            self._start = time.time()
        d.addCallback(_start)

        def _record_uri(uri, i):
            self.uris[i] = uri
        def _upload_one_file(ignored, i):
            if i >= self.count:
                return
            fn = os.path.join(self.basedir, str(i))
            if self.mutable_mode == "create":
                data = open(fn,"rb").read()
                d1 = self.parent.create_mutable_file(data)
                d1.addCallback(lambda n: n.get_uri())
            elif self.mutable_mode == "upload":
                data = open(fn,"rb").read()
                d1 = self._n.overwrite(MutableData(data))
                d1.addCallback(lambda res: self._n.get_uri())
            else:
                up = upload.FileName(fn, convergence=None)
                d1 = self.parent.upload(up)
                d1.addCallback(lambda results: results.get_uri())
            d1.addCallback(_record_uri, i)
            d1.addCallback(_upload_one_file, i+1)
            return d1
        d.addCallback(_upload_one_file, 0)
        def _upload_done(ignored):
            stop = time.time()
            self.upload_time = stop - self._start
        d.addCallback(_upload_done)
        return d

    def do_download(self):
        start = time.time()
        d = defer.succeed(None)
        def _download_one_file(ignored, i):
            if i >= self.count:
                return
            n = self.parent.create_node_from_uri(self.uris[i])
            if not IFileNode.providedBy(n):
                raise AssertionError("The URI does not reference a file.")
            if n.is_mutable():
                d1 = n.download_best_version()
            else:
                d1 = n.read(DiscardingConsumer())
            d1.addCallback(_download_one_file, i+1)
            return d1
        d.addCallback(_download_one_file, 0)
        def _download_done(ignored):
            stop = time.time()
            self.download_time = stop - start
        d.addCallback(_download_done)
        return d

    def do_cleanup(self, res):
        for i in range(self.count):
            fn = os.path.join(self.basedir, str(i))
            os.unlink(fn)
        return res

@implementer(IConsumer)
class DiscardingConsumer(object):
    def __init__(self):
        self.done = False
    def registerProducer(self, p, streaming):
        if streaming:
            p.resumeProducing()
        else:
            while not self.done:
                p.resumeProducing()
    def write(self, data):
        pass
    def unregisterProducer(self):
        self.done = True
