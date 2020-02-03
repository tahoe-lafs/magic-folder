from __future__ import print_function

import os, shutil, sys, urllib, time, stat, urlparse
from six.moves import cStringIO as StringIO
from twisted.internet import defer, reactor, protocol, error
from twisted.application import service, internet
from twisted.web import client as tw_client
from allmydata import client, introducer
from allmydata.immutable import upload
from allmydata.scripts import create_node
from allmydata.util import fileutil, pollmixin
from allmydata.util.fileutil import abspath_expanduser_unicode
from allmydata.util.encodingutil import get_filesystem_encoding
from foolscap.api import Tub, fireEventually, flushEventualQueue
from twisted.python import log, procutils

class StallableHTTPGetterDiscarder(tw_client.HTTPPageGetter, object):
    full_speed_ahead = False
    _bytes_so_far = 0
    stalled = None
    def handleResponsePart(self, data):
        self._bytes_so_far += len(data)
        if not self.factory.do_stall:
            return
        if self.full_speed_ahead:
            return
        if self._bytes_so_far > 1e6+100:
            if not self.stalled:
                print("STALLING")
                self.transport.pauseProducing()
                self.stalled = reactor.callLater(10.0, self._resume_speed)
    def _resume_speed(self):
        print("RESUME SPEED")
        self.stalled = None
        self.full_speed_ahead = True
        self.transport.resumeProducing()
    def handleResponseEnd(self):
        if self.stalled:
            print("CANCEL")
            self.stalled.cancel()
            self.stalled = None
        return tw_client.HTTPPageGetter.handleResponseEnd(self)

class StallableDiscardingHTTPClientFactory(tw_client.HTTPClientFactory, object):
    protocol = StallableHTTPGetterDiscarder

def discardPage(url, stall=False, *args, **kwargs):
    """Start fetching the URL, but stall our pipe after the first 1MB.
    Wait 10 seconds, then resume downloading (and discarding) everything.
    """
    # adapted from twisted.web.client.getPage . We can't just wrap or
    # subclass because it provides no way to override the HTTPClientFactory
    # that it creates.
    scheme, netloc, path, params, query, fragment = urlparse.urlparse(url)
    assert scheme == 'http'
    host, port = netloc, 80
    if ":" in host:
        host, port = host.split(":")
        port = int(port)
    factory = StallableDiscardingHTTPClientFactory(url, *args, **kwargs)
    factory.do_stall = stall
    reactor.connectTCP(host, port, factory)
    return factory.deferred

class ChildDidNotStartError(Exception):
    pass

class SystemFramework(pollmixin.PollMixin):
    numnodes = 7

    def __init__(self, basedir, mode):
        self.basedir = basedir = abspath_expanduser_unicode(unicode(basedir))
        if not (basedir + os.path.sep).startswith(abspath_expanduser_unicode(u".") + os.path.sep):
            raise AssertionError("safety issue: basedir must be a subdir")
        self.testdir = testdir = os.path.join(basedir, "test")
        if os.path.exists(testdir):
            shutil.rmtree(testdir)
        fileutil.make_dirs(testdir)
        self.sparent = service.MultiService()
        self.sparent.startService()
        self.proc = None
        self.tub = Tub()
        self.tub.setOption("expose-remote-exception-types", False)
        self.tub.setServiceParent(self.sparent)
        self.mode = mode
        self.failed = False
        self.keepalive_file = None

    def run(self):
        framelog = os.path.join(self.basedir, "driver.log")
        log.startLogging(open(framelog, "a"), setStdout=False)
        log.msg("CHECK_MEMORY(mode=%s) STARTING" % self.mode)
        #logfile = open(os.path.join(self.testdir, "log"), "w")
        #flo = log.FileLogObserver(logfile)
        #log.startLoggingWithObserver(flo.emit, setStdout=False)
        d = fireEventually()
        d.addCallback(lambda res: self.setUp())
        d.addCallback(lambda res: self.record_initial_memusage())
        d.addCallback(lambda res: self.make_nodes())
        d.addCallback(lambda res: self.wait_for_client_connected())
        d.addCallback(lambda res: self.do_test())
        d.addBoth(self.tearDown)
        def _err(err):
            self.failed = err
            log.err(err)
            print(err)
        d.addErrback(_err)
        def _done(res):
            reactor.stop()
            return res
        d.addBoth(_done)
        reactor.run()
        if self.failed:
            # raiseException doesn't work for CopiedFailures
            self.failed.raiseException()

    def setUp(self):
        #print "STARTING"
        self.stats = {}
        self.statsfile = open(os.path.join(self.basedir, "stats.out"), "a")
        self.make_introducer()
        d = self.start_client()
        def _record_control_furl(control_furl):
            self.control_furl = control_furl
            #print "OBTAINING '%s'" % (control_furl,)
            return self.tub.getReference(self.control_furl)
        d.addCallback(_record_control_furl)
        def _record_control(control_rref):
            self.control_rref = control_rref
        d.addCallback(_record_control)
        def _ready(res):
            #print "CLIENT READY"
            pass
        d.addCallback(_ready)
        return d

    def record_initial_memusage(self):
        print()
        print("Client started (no connections yet)")
        d = self._print_usage()
        d.addCallback(self.stash_stats, "init")
        return d

    def wait_for_client_connected(self):
        print()
        print("Client connecting to other nodes..")
        return self.control_rref.callRemote("wait_for_client_connections",
                                            self.numnodes+1)

    def tearDown(self, passthrough):
        # the client node will shut down in a few seconds
        #os.remove(os.path.join(self.clientdir, client.Client.EXIT_TRIGGER_FILE))
        log.msg("shutting down SystemTest services")
        if self.keepalive_file and os.path.exists(self.keepalive_file):
            age = time.time() - os.stat(self.keepalive_file)[stat.ST_MTIME]
            log.msg("keepalive file at shutdown was %ds old" % age)
        d = defer.succeed(None)
        if self.proc:
            d.addCallback(lambda res: self.kill_client())
        d.addCallback(lambda res: self.sparent.stopService())
        d.addCallback(lambda res: flushEventualQueue())
        def _close_statsfile(res):
            self.statsfile.close()
        d.addCallback(_close_statsfile)
        d.addCallback(lambda res: passthrough)
        return d

    def make_introducer(self):
        iv_basedir = os.path.join(self.testdir, "introducer")
        os.mkdir(iv_basedir)
        self.introducer = introducer.IntroducerNode(basedir=iv_basedir)
        self.introducer.setServiceParent(self)
        self.introducer_furl = self.introducer.introducer_url

    def make_nodes(self):
        self.nodes = []
        for i in range(self.numnodes):
            nodedir = os.path.join(self.testdir, "node%d" % i)
            os.mkdir(nodedir)
            f = open(os.path.join(nodedir, "tahoe.cfg"), "w")
            f.write("[client]\n"
                    "introducer.furl = %s\n"
                    "shares.happy = 1\n"
                    "[storage]\n"
                    % (self.introducer_furl,))
            # the only tests for which we want the internal nodes to actually
            # retain shares are the ones where somebody's going to download
            # them.
            if self.mode in ("download", "download-GET", "download-GET-slow"):
                # retain shares
                pass
            else:
                # for these tests, we tell the storage servers to pretend to
                # accept shares, but really just throw them out, since we're
                # only testing upload and not download.
                f.write("debug_discard = true\n")
            if self.mode in ("receive",):
                # for this mode, the client-under-test gets all the shares,
                # so our internal nodes can refuse requests
                f.write("readonly = true\n")
            f.close()
            c = client.Client(basedir=nodedir)
            c.setServiceParent(self)
            self.nodes.append(c)
        # the peers will start running, eventually they will connect to each
        # other and the introducer

    def touch_keepalive(self):
        if os.path.exists(self.keepalive_file):
            age = time.time() - os.stat(self.keepalive_file)[stat.ST_MTIME]
            log.msg("touching keepalive file, was %ds old" % age)
        f = open(self.keepalive_file, "w")
        f.write("""\
If the node notices this file at startup, it will poll every 5 seconds and
terminate if the file is more than 10 seconds old, or if it has been deleted.
If the test harness has an internal failure and neglects to kill off the node
itself, this helps to avoid leaving processes lying around. The contents of
this file are ignored.
        """)
        f.close()

    def start_client(self):
        # this returns a Deferred that fires with the client's control.furl
        log.msg("MAKING CLIENT")
        # self.testdir is an absolute Unicode path
        clientdir = self.clientdir = os.path.join(self.testdir, u"client")
        clientdir_str = clientdir.encode(get_filesystem_encoding())
        quiet = StringIO()
        create_node.create_node({'basedir': clientdir}, out=quiet)
        log.msg("DONE MAKING CLIENT")
        # now replace tahoe.cfg
        # set webport=0 and then ask the node what port it picked.
        f = open(os.path.join(clientdir, "tahoe.cfg"), "w")
        f.write("[node]\n"
                "web.port = tcp:0:interface=127.0.0.1\n"
                "[client]\n"
                "introducer.furl = %s\n"
                "shares.happy = 1\n"
                "[storage]\n"
                % (self.introducer_furl,))

        if self.mode in ("upload-self", "receive"):
            # accept and store shares, to trigger the memory consumption bugs
            pass
        else:
            # don't accept any shares
            f.write("readonly = true\n")
            ## also, if we do receive any shares, throw them away
            #f.write("debug_discard = true")
        if self.mode == "upload-self":
            pass
        f.close()
        self.keepalive_file = os.path.join(clientdir,
                                           client.Client.EXIT_TRIGGER_FILE)
        # now start updating the mtime.
        self.touch_keepalive()
        ts = internet.TimerService(1.0, self.touch_keepalive)
        ts.setServiceParent(self.sparent)

        pp = ClientWatcher()
        self.proc_done = pp.d = defer.Deferred()
        logfile = os.path.join(self.basedir, "client.log")
        tahoes = procutils.which("tahoe")
        if not tahoes:
            raise RuntimeError("unable to find a 'tahoe' executable")
        cmd = [tahoes[0], "run", ".", "-l", logfile]
        env = os.environ.copy()
        self.proc = reactor.spawnProcess(pp, cmd[0], cmd, env, path=clientdir_str)
        log.msg("CLIENT STARTED")

        # now we wait for the client to get started. we're looking for the
        # control.furl file to appear.
        furl_file = os.path.join(clientdir, "private", "control.furl")
        url_file = os.path.join(clientdir, "node.url")
        def _check():
            if pp.ended and pp.ended.value.status != 0:
                # the twistd process ends normally (with rc=0) if the child
                # is successfully launched. It ends abnormally (with rc!=0)
                # if the child cannot be launched.
                raise ChildDidNotStartError("process ended while waiting for startup")
            return os.path.exists(furl_file)
        d = self.poll(_check, 0.1)
        # once it exists, wait a moment before we read from it, just in case
        # it hasn't finished writing the whole thing. Ideally control.furl
        # would be created in some atomic fashion, or made non-readable until
        # it's ready, but I can't think of an easy way to do that, and I
        # think the chances that we'll observe a half-write are pretty low.
        def _stall(res):
            d2 = defer.Deferred()
            reactor.callLater(0.1, d2.callback, None)
            return d2
        d.addCallback(_stall)
        def _read(res):
            # read the node's URL
            self.webish_url = open(url_file, "r").read().strip()
            if self.webish_url[-1] == "/":
                # trim trailing slash, since the rest of the code wants it gone
                self.webish_url = self.webish_url[:-1]
            f = open(furl_file, "r")
            furl = f.read()
            return furl.strip()
        d.addCallback(_read)
        return d


    def kill_client(self):
        # returns a Deferred that fires when the process exits. This may only
        # be called once.
        try:
            self.proc.signalProcess("INT")
        except error.ProcessExitedAlready:
            pass
        return self.proc_done


    def create_data(self, name, size):
        filename = os.path.join(self.testdir, name + ".data")
        f = open(filename, "wb")
        block = "a" * 8192
        while size > 0:
            l = min(size, 8192)
            f.write(block[:l])
            size -= l
        return filename

    def stash_stats(self, stats, name):
        self.statsfile.write("%s %s: %d\n" % (self.mode, name, stats['VmPeak']))
        self.statsfile.flush()
        self.stats[name] = stats['VmPeak']

    def POST(self, urlpath, **fields):
        url = self.webish_url + urlpath
        sepbase = "boogabooga"
        sep = "--" + sepbase
        form = []
        form.append(sep)
        form.append('Content-Disposition: form-data; name="_charset"')
        form.append('')
        form.append('UTF-8')
        form.append(sep)
        for name, value in fields.iteritems():
            if isinstance(value, tuple):
                filename, value = value
                form.append('Content-Disposition: form-data; name="%s"; '
                            'filename="%s"' % (name, filename))
            else:
                form.append('Content-Disposition: form-data; name="%s"' % name)
            form.append('')
            form.append(value)
            form.append(sep)
        form[-1] += "--"
        body = "\r\n".join(form) + "\r\n"
        headers = {"content-type": "multipart/form-data; boundary=%s" % sepbase,
                   }
        return tw_client.getPage(url, method="POST", postdata=body,
                                 headers=headers, followRedirect=False)

    def GET_discard(self, urlpath, stall):
        url = self.webish_url + urlpath + "?filename=dummy-get.out"
        return discardPage(url, stall)

    def _print_usage(self, res=None):
        d = self.control_rref.callRemote("get_memory_usage")
        def _print(stats):
            print("VmSize: %9d  VmPeak: %9d" % (stats["VmSize"],
                                                stats["VmPeak"]))
            return stats
        d.addCallback(_print)
        return d

    def _do_upload(self, res, size, files, uris):
        name = '%d' % size
        print()
        print("uploading %s" % name)
        if self.mode in ("upload", "upload-self"):
            d = self.control_rref.callRemote("upload_random_data_from_file",
                                             size,
                                             convergence="check-memory")
        elif self.mode == "upload-POST":
            data = "a" * size
            url = "/uri"
            d = self.POST(url, t="upload", file=("%d.data" % size, data))
        elif self.mode in ("receive",
                           "download", "download-GET", "download-GET-slow"):
            # mode=receive: upload the data from a local peer, so that the
            # client-under-test receives and stores the shares
            #
            # mode=download*: upload the data from a local peer, then have
            # the client-under-test download it.
            #
            # we need to wait until the uploading node has connected to all
            # peers, since the wait_for_client_connections() above doesn't
            # pay attention to our self.nodes[] and their connections.
            files[name] = self.create_data(name, size)
            u = self.nodes[0].getServiceNamed("uploader")
            d = self.nodes[0].debug_wait_for_client_connections(self.numnodes+1)
            d.addCallback(lambda res:
                          u.upload(upload.FileName(files[name],
                                                   convergence="check-memory")))
            d.addCallback(lambda results: results.get_uri())
        else:
            raise ValueError("unknown mode=%s" % self.mode)
        def _complete(uri):
            uris[name] = uri
            print("uploaded %s" % name)
        d.addCallback(_complete)
        return d

    def _do_download(self, res, size, uris):
        if self.mode not in ("download", "download-GET", "download-GET-slow"):
            return
        name = '%d' % size
        print("downloading %s" % name)
        uri = uris[name]

        if self.mode == "download":
            d = self.control_rref.callRemote("download_to_tempfile_and_delete",
                                             uri)
        elif self.mode == "download-GET":
            url = "/uri/%s" % uri
            d = self.GET_discard(urllib.quote(url), stall=False)
        elif self.mode == "download-GET-slow":
            url = "/uri/%s" % uri
            d = self.GET_discard(urllib.quote(url), stall=True)

        def _complete(res):
            print("downloaded %s" % name)
            return res
        d.addCallback(_complete)
        return d

    def do_test(self):
        #print "CLIENT STARTED"
        #print "FURL", self.control_furl
        #print "RREF", self.control_rref
        #print
        kB = 1000; MB = 1000*1000
        files = {}
        uris = {}

        d = self._print_usage()
        d.addCallback(self.stash_stats, "0B")

        for i in range(10):
            d.addCallback(self._do_upload, 10*kB+i, files, uris)
            d.addCallback(self._do_download, 10*kB+i, uris)
            d.addCallback(self._print_usage)
        d.addCallback(self.stash_stats, "10kB")

        for i in range(3):
            d.addCallback(self._do_upload, 10*MB+i, files, uris)
            d.addCallback(self._do_download, 10*MB+i, uris)
            d.addCallback(self._print_usage)
        d.addCallback(self.stash_stats, "10MB")

        for i in range(1):
            d.addCallback(self._do_upload, 50*MB+i, files, uris)
            d.addCallback(self._do_download, 50*MB+i, uris)
            d.addCallback(self._print_usage)
        d.addCallback(self.stash_stats, "50MB")

        #for i in range(1):
        #    d.addCallback(self._do_upload, 100*MB+i, files, uris)
        #    d.addCallback(self._do_download, 100*MB+i, uris)
        #    d.addCallback(self._print_usage)
        #d.addCallback(self.stash_stats, "100MB")

        #d.addCallback(self.stall)
        def _done(res):
            print("FINISHING")
        d.addCallback(_done)
        return d

    def stall(self, res):
        d = defer.Deferred()
        reactor.callLater(5, d.callback, None)
        return d


class ClientWatcher(protocol.ProcessProtocol, object):
    ended = False
    def outReceived(self, data):
        print("OUT:", data)
    def errReceived(self, data):
        print("ERR:", data)
    def processEnded(self, reason):
        self.ended = reason
        self.d.callback(None)


if __name__ == '__main__':
    mode = "upload"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    if sys.maxint == 2147483647:
        bits = "32"
    elif sys.maxint == 9223372036854775807:
        bits = "64"
    else:
        bits = "?"
    print("%s-bit system (sys.maxint=%d)" % (bits, sys.maxint))
    # put the logfile and stats.out in _test_memory/ . These stick around.
    # put the nodes and other files in _test_memory/test/ . These are
    # removed each time we run.
    sf = SystemFramework("_test_memory", mode)
    sf.run()
