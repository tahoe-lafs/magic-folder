
import os, json, urllib
from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet.defer import inlineCallbacks, returnValue
from allmydata.immutable import upload
from allmydata.mutable.common import UnrecoverableFileError
from allmydata.mutable.publish import MutableData
from allmydata.util import idlib
from allmydata.util import base32
from allmydata.interfaces import ICheckResults, ICheckAndRepairResults, \
     IDeepCheckResults, IDeepCheckAndRepairResults
from allmydata.monitor import Monitor, OperationCancelledError
from allmydata.uri import LiteralFileURI

from allmydata.test.common import ErrorMixin, _corrupt_mutable_share_data, \
     ShouldFailMixin
from .common_util import StallMixin, run_cli
from .common_web import do_http
from allmydata.test.no_network import GridTestMixin
from .cli.common import CLITestMixin

class MutableChecker(GridTestMixin, unittest.TestCase, ErrorMixin):
    def test_good(self):
        self.basedir = "deepcheck/MutableChecker/good"
        self.set_up_grid()
        CONTENTS = "a little bit of data"
        CONTENTS_uploadable = MutableData(CONTENTS)
        d = self.g.clients[0].create_mutable_file(CONTENTS_uploadable)
        def _created(node):
            self.node = node
            self.fileurl = "uri/" + urllib.quote(node.get_uri())
        d.addCallback(_created)
        # now make sure the webapi verifier sees no problems
        d.addCallback(lambda ign: self.GET(self.fileurl+"?t=check&verify=true",
                                           method="POST"))
        def _got_results(out):
            self.failUnless("<span>Healthy : Healthy</span>" in out, out)
            self.failUnless("Recoverable Versions: 10*seq1-" in out, out)
            self.failIf("Not Healthy!" in out, out)
            self.failIf("Unhealthy" in out, out)
            self.failIf("Corrupt Shares" in out, out)
        d.addCallback(_got_results)
        d.addErrback(self.explain_web_error)
        return d

    def test_corrupt(self):
        self.basedir = "deepcheck/MutableChecker/corrupt"
        self.set_up_grid()
        CONTENTS = "a little bit of data"
        CONTENTS_uploadable = MutableData(CONTENTS)
        d = self.g.clients[0].create_mutable_file(CONTENTS_uploadable)
        def _stash_and_corrupt(node):
            self.node = node
            self.fileurl = "uri/" + urllib.quote(node.get_uri())
            self.corrupt_shares_numbered(node.get_uri(), [0],
                                         _corrupt_mutable_share_data)
        d.addCallback(_stash_and_corrupt)
        # now make sure the webapi verifier notices it
        d.addCallback(lambda ign: self.GET(self.fileurl+"?t=check&verify=true",
                                           method="POST"))
        def _got_results(out):
            self.failUnless("Not Healthy!" in out, out)
            self.failUnless("Unhealthy: best version has only 9 shares (encoding is 3-of-10)" in out, out)
            self.failUnless("Corrupt Shares:" in out, out)
        d.addCallback(_got_results)

        # now make sure the webapi repairer can fix it
        d.addCallback(lambda ign:
                      self.GET(self.fileurl+"?t=check&verify=true&repair=true",
                               method="POST"))
        def _got_repair_results(out):
            self.failUnless("<div>Repair successful</div>" in out, out)
        d.addCallback(_got_repair_results)
        d.addCallback(lambda ign: self.GET(self.fileurl+"?t=check&verify=true",
                                           method="POST"))
        def _got_postrepair_results(out):
            self.failIf("Not Healthy!" in out, out)
            self.failUnless("Recoverable Versions: 10*seq" in out, out)
        d.addCallback(_got_postrepair_results)
        d.addErrback(self.explain_web_error)

        return d

    def test_delete_share(self):
        self.basedir = "deepcheck/MutableChecker/delete_share"
        self.set_up_grid()
        CONTENTS = "a little bit of data"
        CONTENTS_uploadable = MutableData(CONTENTS)
        d = self.g.clients[0].create_mutable_file(CONTENTS_uploadable)
        def _stash_and_delete(node):
            self.node = node
            self.fileurl = "uri/" + urllib.quote(node.get_uri())
            self.delete_shares_numbered(node.get_uri(), [0])
        d.addCallback(_stash_and_delete)
        # now make sure the webapi checker notices it
        d.addCallback(lambda ign: self.GET(self.fileurl+"?t=check&verify=false",
                                           method="POST"))
        def _got_results(out):
            self.failUnless("Not Healthy!" in out, out)
            self.failUnless("Unhealthy: best version has only 9 shares (encoding is 3-of-10)" in out, out)
            self.failIf("Corrupt Shares" in out, out)
        d.addCallback(_got_results)

        # now make sure the webapi repairer can fix it
        d.addCallback(lambda ign:
                      self.GET(self.fileurl+"?t=check&verify=false&repair=true",
                               method="POST"))
        def _got_repair_results(out):
            self.failUnless("Repair successful" in out)
        d.addCallback(_got_repair_results)
        d.addCallback(lambda ign: self.GET(self.fileurl+"?t=check&verify=false",
                                           method="POST"))
        def _got_postrepair_results(out):
            self.failIf("Not Healthy!" in out, out)
            self.failUnless("Recoverable Versions: 10*seq" in out)
        d.addCallback(_got_postrepair_results)
        d.addErrback(self.explain_web_error)

        return d


class DeepCheckBase(GridTestMixin, ErrorMixin, StallMixin, ShouldFailMixin,
                    CLITestMixin):

    def web_json(self, n, **kwargs):
        kwargs["output"] = "json"
        d = self.web(n, "POST", **kwargs)
        d.addCallback(self.decode_json)
        return d

    def decode_json(self, args):
        (s, url) = args
        try:
            data = json.loads(s)
        except ValueError:
            self.fail("%s: not JSON: '%s'" % (url, s))
        return data

    def parse_streamed_json(self, s):
        for unit in s.split("\n"):
            if not unit:
                # stream should end with a newline, so split returns ""
                continue
            try:
                yield json.loads(unit)
            except ValueError as le:
                le.args = tuple(le.args + (unit,))
                raise

    @inlineCallbacks
    def web(self, n, method="GET", **kwargs):
        # returns (data, url)
        url = (self.client_baseurls[0] + "uri/%s" % urllib.quote(n.get_uri())
               + "?" + "&".join(["%s=%s" % (k,v) for (k,v) in kwargs.items()]))
        data = yield do_http(method, url, browser_like_redirects=True)
        returnValue((data,url))

    @inlineCallbacks
    def wait_for_operation(self, ophandle):
        url = self.client_baseurls[0] + "operations/" + ophandle
        url += "?t=status&output=JSON"
        while True:
            body = yield do_http("get", url)
            data = json.loads(body)
            if data["finished"]:
                break
            yield self.stall(delay=0.1)
        returnValue(data)

    @inlineCallbacks
    def get_operation_results(self, ophandle, output=None):
        url = self.client_baseurls[0] + "operations/" + ophandle
        url += "?t=status"
        if output:
            url += "&output=" + output
        body = yield do_http("get", url)
        if output and output.lower() == "json":
            data = json.loads(body)
        else:
            data = body
        returnValue(data)

    @inlineCallbacks
    def slow_web(self, n, output=None, **kwargs):
        # use ophandle=
        handle = base32.b2a(os.urandom(4))
        yield self.web(n, "POST", ophandle=handle, **kwargs)
        yield self.wait_for_operation(handle)
        data = yield self.get_operation_results(handle, output=output)
        returnValue(data)


class DeepCheckWebGood(DeepCheckBase, unittest.TestCase):
    # construct a small directory tree (with one dir, one immutable file, one
    # mutable file, two LIT files, one DIR2:LIT empty dir, one DIR2:LIT tiny
    # dir, and a loop), and then check/examine it in various ways.

    def set_up_tree(self):
        # 2.9s

        c0 = self.g.clients[0]
        d = c0.create_dirnode()
        def _created_root(n):
            self.root = n
            self.root_uri = n.get_uri()
        d.addCallback(_created_root)
        d.addCallback(lambda ign:
            c0.create_mutable_file(MutableData("mutable file contents")))
        d.addCallback(lambda n: self.root.set_node(u"mutable", n))
        def _created_mutable(n):
            self.mutable = n
            self.mutable_uri = n.get_uri()
        d.addCallback(_created_mutable)

        large = upload.Data("Lots of data\n" * 1000, None)
        d.addCallback(lambda ign: self.root.add_file(u"large", large))
        def _created_large(n):
            self.large = n
            self.large_uri = n.get_uri()
        d.addCallback(_created_large)

        small = upload.Data("Small enough for a LIT", None)
        d.addCallback(lambda ign: self.root.add_file(u"small", small))
        def _created_small(n):
            self.small = n
            self.small_uri = n.get_uri()
        d.addCallback(_created_small)

        small2 = upload.Data("Small enough for a LIT too", None)
        d.addCallback(lambda ign: self.root.add_file(u"small2", small2))
        def _created_small2(n):
            self.small2 = n
            self.small2_uri = n.get_uri()
        d.addCallback(_created_small2)

        empty_litdir_uri = "URI:DIR2-LIT:"
        tiny_litdir_uri = "URI:DIR2-LIT:gqytunj2onug64tufqzdcosvkjetutcjkq5gw4tvm5vwszdgnz5hgyzufqydulbshj5x2lbm" # contains one child which is itself also LIT

        d.addCallback(lambda ign: self.root._create_and_validate_node(None, empty_litdir_uri, name=u"test_deepcheck empty_lit_dir"))
        def _created_empty_lit_dir(n):
            self.empty_lit_dir = n
            self.empty_lit_dir_uri = n.get_uri()
            self.root.set_node(u"empty_lit_dir", n)
        d.addCallback(_created_empty_lit_dir)

        d.addCallback(lambda ign: self.root._create_and_validate_node(None, tiny_litdir_uri, name=u"test_deepcheck tiny_lit_dir"))
        def _created_tiny_lit_dir(n):
            self.tiny_lit_dir = n
            self.tiny_lit_dir_uri = n.get_uri()
            self.root.set_node(u"tiny_lit_dir", n)
        d.addCallback(_created_tiny_lit_dir)

        d.addCallback(lambda ign: self.root.set_node(u"loop", self.root))
        return d

    def check_is_healthy(self, cr, n, where, incomplete=False):
        self.failUnless(ICheckResults.providedBy(cr), where)
        self.failUnless(cr.is_healthy(), where)
        self.failUnlessEqual(cr.get_storage_index(), n.get_storage_index(),
                             where)
        self.failUnlessEqual(cr.get_storage_index_string(),
                             base32.b2a(n.get_storage_index()), where)
        num_servers = len(self.g.all_servers)
        self.failUnlessEqual(num_servers, 10, where)

        self.failUnlessEqual(cr.get_happiness(), num_servers, where)
        self.failUnlessEqual(cr.get_share_counter_good(), num_servers, where)
        self.failUnlessEqual(cr.get_encoding_needed(), 3, where)
        self.failUnlessEqual(cr.get_encoding_expected(), num_servers, where)
        if not incomplete:
            self.failUnlessEqual(cr.get_host_counter_good_shares(),
                                 num_servers, where)
        self.failUnlessEqual(cr.get_corrupt_shares(), [], where)
        if not incomplete:
            self.failUnlessEqual(sorted([s.get_serverid()
                                         for s in cr.get_servers_responding()]),
                                 sorted(self.g.get_all_serverids()),
                                 where)
            all_serverids = set()
            for (shareid, servers) in cr.get_sharemap().items():
                all_serverids.update([s.get_serverid() for s in servers])
            self.failUnlessEqual(sorted(all_serverids),
                                 sorted(self.g.get_all_serverids()),
                                 where)

        self.failUnlessEqual(cr.get_share_counter_wrong(), 0, where)
        self.failUnlessEqual(cr.get_version_counter_recoverable(), 1, where)
        self.failUnlessEqual(cr.get_version_counter_unrecoverable(), 0, where)


    def check_and_repair_is_healthy(self, cr, n, where, incomplete=False):
        self.failUnless(ICheckAndRepairResults.providedBy(cr), (where, cr))
        self.failUnless(cr.get_pre_repair_results().is_healthy(), where)
        self.check_is_healthy(cr.get_pre_repair_results(), n, where, incomplete)
        self.failUnless(cr.get_post_repair_results().is_healthy(), where)
        self.check_is_healthy(cr.get_post_repair_results(), n, where, incomplete)
        self.failIf(cr.get_repair_attempted(), where)

    def deep_check_is_healthy(self, cr, num_healthy, where):
        self.failUnless(IDeepCheckResults.providedBy(cr))
        self.failUnlessEqual(cr.get_counters()["count-objects-healthy"],
                             num_healthy, where)

    def deep_check_and_repair_is_healthy(self, cr, num_healthy, where):
        self.failUnless(IDeepCheckAndRepairResults.providedBy(cr), where)
        c = cr.get_counters()
        self.failUnlessEqual(c["count-objects-healthy-pre-repair"],
                             num_healthy, where)
        self.failUnlessEqual(c["count-objects-healthy-post-repair"],
                             num_healthy, where)
        self.failUnlessEqual(c["count-repairs-attempted"], 0, where)

    def test_good(self):
        self.basedir = "deepcheck/DeepCheckWebGood/good"
        self.set_up_grid()
        d = self.set_up_tree()
        d.addCallback(self.do_stats)
        d.addCallback(self.do_web_stream_manifest)
        d.addCallback(self.do_web_stream_check)
        d.addCallback(self.do_test_check_good)
        d.addCallback(self.do_test_web_good)
        d.addCallback(self.do_test_cli_good)
        d.addErrback(self.explain_web_error)
        d.addErrback(self.explain_error)
        return d

    def do_stats(self, ignored):
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.root.start_deep_stats().when_done())
        d.addCallback(self.check_stats_good)
        return d

    def check_stats_good(self, s):
        self.failUnlessEqual(s["count-directories"], 3)
        self.failUnlessEqual(s["count-files"], 5)
        self.failUnlessEqual(s["count-immutable-files"], 1)
        self.failUnlessEqual(s["count-literal-files"], 3)
        self.failUnlessEqual(s["count-mutable-files"], 1)
        # don't check directories: their size will vary
        # s["largest-directory"]
        # s["size-directories"]
        self.failUnlessEqual(s["largest-directory-children"], 7)
        self.failUnlessEqual(s["largest-immutable-file"], 13000)
        # to re-use this function for both the local
        # dirnode.start_deep_stats() and the webapi t=start-deep-stats, we
        # coerce the result into a list of tuples. dirnode.start_deep_stats()
        # returns a list of tuples, but JSON only knows about lists., so
        # t=start-deep-stats returns a list of lists.
        histogram = [tuple(stuff) for stuff in s["size-files-histogram"]]
        self.failUnlessEqual(histogram, [(4, 10, 1), (11, 31, 2),
                                         (10001, 31622, 1),
                                         ])
        self.failUnlessEqual(s["size-immutable-files"], 13000)
        self.failUnlessEqual(s["size-literal-files"], 56)

    def do_web_stream_manifest(self, ignored):
        d = self.web(self.root, method="POST", t="stream-manifest")
        d.addCallback(lambda output_and_url:
                      self._check_streamed_manifest(output_and_url[0]))
        return d

    def _check_streamed_manifest(self, output):
        units = list(self.parse_streamed_json(output))
        files = [u for u in units if u["type"] in ("file", "directory")]
        assert units[-1]["type"] == "stats"
        stats = units[-1]["stats"]
        self.failUnlessEqual(len(files), 8)
        # [root,mutable,large] are distributed, [small,small2,empty_litdir,tiny_litdir] are not
        self.failUnlessEqual(len([f for f in files
                                  if f["verifycap"] != ""]), 3)
        self.failUnlessEqual(len([f for f in files
                                  if f["verifycap"] == ""]), 5)
        self.failUnlessEqual(len([f for f in files
                                  if f["repaircap"] != ""]), 3)
        self.failUnlessEqual(len([f for f in files
                                  if f["repaircap"] == ""]), 5)
        self.failUnlessEqual(len([f for f in files
                                  if f["storage-index"] != ""]), 3)
        self.failUnlessEqual(len([f for f in files
                                  if f["storage-index"] == ""]), 5)
        # make sure that a mutable file has filecap==repaircap!=verifycap
        mutable = [f for f in files
                   if f["cap"] is not None
                   and f["cap"].startswith("URI:SSK:")][0]
        self.failUnlessEqual(mutable["cap"], self.mutable_uri)
        self.failIfEqual(mutable["cap"], mutable["verifycap"])
        self.failUnlessEqual(mutable["cap"], mutable["repaircap"])
        # for immutable file, verifycap==repaircap!=filecap
        large = [f for f in files
                   if f["cap"] is not None
                   and f["cap"].startswith("URI:CHK:")][0]
        self.failUnlessEqual(large["cap"], self.large_uri)
        self.failIfEqual(large["cap"], large["verifycap"])
        self.failUnlessEqual(large["verifycap"], large["repaircap"])
        self.check_stats_good(stats)

    def do_web_stream_check(self, ignored):
        # TODO
        return
        d = self.web(self.root, t="stream-deep-check")
        def _check(res):
            units = list(self.parse_streamed_json(res))
            #files = [u for u in units if u["type"] in ("file", "directory")]
            assert units[-1]["type"] == "stats"
            #stats = units[-1]["stats"]
            # ...
        d.addCallback(_check)
        return d

    def do_test_check_good(self, ignored):
        d = defer.succeed(None)
        # check the individual items
        d.addCallback(lambda ign: self.root.check(Monitor()))
        d.addCallback(self.check_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check(Monitor()))
        d.addCallback(self.check_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check(Monitor()))
        d.addCallback(self.check_is_healthy, self.large, "large")
        d.addCallback(lambda ign: self.small.check(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small2")
        d.addCallback(lambda ign: self.empty_lit_dir.check(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "empty_lit_dir")
        d.addCallback(lambda ign: self.tiny_lit_dir.check(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "tiny_lit_dir")

        # and again with verify=True
        d.addCallback(lambda ign: self.root.check(Monitor(), verify=True))
        d.addCallback(self.check_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check(Monitor(), verify=True))
        d.addCallback(self.check_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check(Monitor(), verify=True))
        d.addCallback(self.check_is_healthy, self.large, "large", incomplete=True)
        d.addCallback(lambda ign: self.small.check(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small2")
        d.addCallback(lambda ign: self.empty_lit_dir.check(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "empty_lit_dir")
        d.addCallback(lambda ign: self.tiny_lit_dir.check(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "tiny_lit_dir")

        # and check_and_repair(), which should be a nop
        d.addCallback(lambda ign: self.root.check_and_repair(Monitor()))
        d.addCallback(self.check_and_repair_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check_and_repair(Monitor()))
        d.addCallback(self.check_and_repair_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check_and_repair(Monitor()))
        d.addCallback(self.check_and_repair_is_healthy, self.large, "large")
        d.addCallback(lambda ign: self.small.check_and_repair(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check_and_repair(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "small2")
        d.addCallback(lambda ign: self.empty_lit_dir.check_and_repair(Monitor()))
        d.addCallback(self.failUnlessEqual, None, "empty_lit_dir")
        d.addCallback(lambda ign: self.tiny_lit_dir.check_and_repair(Monitor()))

        # check_and_repair(verify=True)
        d.addCallback(lambda ign: self.root.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.check_and_repair_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.mutable.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.check_and_repair_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.large.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.check_and_repair_is_healthy, self.large, "large", incomplete=True)
        d.addCallback(lambda ign: self.small.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small")
        d.addCallback(lambda ign: self.small2.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "small2")
        d.addCallback(self.failUnlessEqual, None, "small2")
        d.addCallback(lambda ign: self.empty_lit_dir.check_and_repair(Monitor(), verify=True))
        d.addCallback(self.failUnlessEqual, None, "empty_lit_dir")
        d.addCallback(lambda ign: self.tiny_lit_dir.check_and_repair(Monitor(), verify=True))


        # now deep-check the root, with various verify= and repair= options
        d.addCallback(lambda ign:
                      self.root.start_deep_check().when_done())
        d.addCallback(self.deep_check_is_healthy, 3, "root")
        d.addCallback(lambda ign:
                      self.root.start_deep_check(verify=True).when_done())
        d.addCallback(self.deep_check_is_healthy, 3, "root")
        d.addCallback(lambda ign:
                      self.root.start_deep_check_and_repair().when_done())
        d.addCallback(self.deep_check_and_repair_is_healthy, 3, "root")
        d.addCallback(lambda ign:
                      self.root.start_deep_check_and_repair(verify=True).when_done())
        d.addCallback(self.deep_check_and_repair_is_healthy, 3, "root")

        # and finally, start a deep-check, but then cancel it.
        d.addCallback(lambda ign: self.root.start_deep_check())
        def _checking(monitor):
            monitor.cancel()
            d = monitor.when_done()
            # this should fire as soon as the next dirnode.list finishes.
            # TODO: add a counter to measure how many list() calls are made,
            # assert that no more than one gets to run before the cancel()
            # takes effect.
            def _finished_normally(res):
                self.fail("this was supposed to fail, not finish normally")
            def _cancelled(f):
                f.trap(OperationCancelledError)
            d.addCallbacks(_finished_normally, _cancelled)
            return d
        d.addCallback(_checking)

        return d

    def json_check_is_healthy(self, data, n, where, incomplete=False):

        self.failUnlessEqual(data["storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnless("summary" in data, (where, data))
        self.failUnlessEqual(data["summary"].lower(), "healthy",
                             "%s: '%s'" % (where, data["summary"]))
        r = data["results"]
        self.failUnlessEqual(r["healthy"], True, where)
        num_servers = len(self.g.all_servers)
        self.failUnlessEqual(num_servers, 10)

        self.failIfIn("needs-rebalancing", r)
        self.failUnlessEqual(r["count-happiness"], num_servers, where)
        self.failUnlessEqual(r["count-shares-good"], num_servers, where)
        self.failUnlessEqual(r["count-shares-needed"], 3, where)
        self.failUnlessEqual(r["count-shares-expected"], num_servers, where)
        if not incomplete:
            self.failUnlessEqual(r["count-good-share-hosts"], num_servers,
                                 where)
        self.failUnlessEqual(r["count-corrupt-shares"], 0, where)
        self.failUnlessEqual(r["list-corrupt-shares"], [], where)
        if not incomplete:
            self.failUnlessEqual(sorted(r["servers-responding"]),
                                 sorted([idlib.nodeid_b2a(sid)
                                         for sid in self.g.get_all_serverids()]),
                                 where)
            self.failUnless("sharemap" in r, where)
            all_serverids = set()
            for (shareid, serverids_s) in r["sharemap"].items():
                all_serverids.update(serverids_s)
            self.failUnlessEqual(sorted(all_serverids),
                                 sorted([idlib.nodeid_b2a(sid)
                                         for sid in self.g.get_all_serverids()]),
                                 where)
        self.failUnlessEqual(r["count-wrong-shares"], 0, where)
        self.failUnlessEqual(r["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 0, where)

    def json_check_and_repair_is_healthy(self, data, n, where, incomplete=False):
        self.failUnlessEqual(data["storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnlessEqual(data["repair-attempted"], False, where)
        self.json_check_is_healthy(data["pre-repair-results"],
                                   n, where, incomplete)
        self.json_check_is_healthy(data["post-repair-results"],
                                   n, where, incomplete)

    def json_full_deepcheck_is_healthy(self, data, n, where):
        self.failUnlessEqual(data["root-storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnlessEqual(data["count-objects-checked"], 3, where)
        self.failUnlessEqual(data["count-objects-healthy"], 3, where)
        self.failUnlessEqual(data["count-objects-unhealthy"], 0, where)
        self.failUnlessEqual(data["count-corrupt-shares"], 0, where)
        self.failUnlessEqual(data["list-corrupt-shares"], [], where)
        self.failUnlessEqual(data["list-unhealthy-files"], [], where)
        self.json_check_stats_good(data["stats"], where)

    def json_full_deepcheck_and_repair_is_healthy(self, data, n, where):
        self.failUnlessEqual(data["root-storage-index"],
                             base32.b2a(n.get_storage_index()), where)
        self.failUnlessEqual(data["count-objects-checked"], 3, where)

        self.failUnlessEqual(data["count-objects-healthy-pre-repair"], 3, where)
        self.failUnlessEqual(data["count-objects-unhealthy-pre-repair"], 0, where)
        self.failUnlessEqual(data["count-corrupt-shares-pre-repair"], 0, where)

        self.failUnlessEqual(data["count-objects-healthy-post-repair"], 3, where)
        self.failUnlessEqual(data["count-objects-unhealthy-post-repair"], 0, where)
        self.failUnlessEqual(data["count-corrupt-shares-post-repair"], 0, where)

        self.failUnlessEqual(data["list-corrupt-shares"], [], where)
        self.failUnlessEqual(data["list-remaining-corrupt-shares"], [], where)
        self.failUnlessEqual(data["list-unhealthy-files"], [], where)

        self.failUnlessEqual(data["count-repairs-attempted"], 0, where)
        self.failUnlessEqual(data["count-repairs-successful"], 0, where)
        self.failUnlessEqual(data["count-repairs-unsuccessful"], 0, where)


    def json_check_lit(self, data, n, where):
        self.failUnlessEqual(data["storage-index"], "", where)
        self.failUnlessEqual(data["results"]["healthy"], True, where)

    def json_check_stats_good(self, data, where):
        self.check_stats_good(data)

    def do_test_web_good(self, ignored):
        d = defer.succeed(None)

        # stats
        d.addCallback(lambda ign:
                      self.slow_web(self.root,
                                    t="start-deep-stats", output="json"))
        d.addCallback(self.json_check_stats_good, "deep-stats")

        # check, no verify
        d.addCallback(lambda ign: self.web_json(self.root, t="check"))
        d.addCallback(self.json_check_is_healthy, self.root, "root")
        d.addCallback(lambda ign: self.web_json(self.mutable, t="check"))
        d.addCallback(self.json_check_is_healthy, self.mutable, "mutable")
        d.addCallback(lambda ign: self.web_json(self.large, t="check"))
        d.addCallback(self.json_check_is_healthy, self.large, "large")
        d.addCallback(lambda ign: self.web_json(self.small, t="check"))
        d.addCallback(self.json_check_lit, self.small, "small")
        d.addCallback(lambda ign: self.web_json(self.small2, t="check"))
        d.addCallback(self.json_check_lit, self.small2, "small2")
        d.addCallback(lambda ign: self.web_json(self.empty_lit_dir, t="check"))
        d.addCallback(self.json_check_lit, self.empty_lit_dir, "empty_lit_dir")
        d.addCallback(lambda ign: self.web_json(self.tiny_lit_dir, t="check"))
        d.addCallback(self.json_check_lit, self.tiny_lit_dir, "tiny_lit_dir")

        # check and verify
        d.addCallback(lambda ign:
                      self.web_json(self.root, t="check", verify="true"))
        d.addCallback(self.json_check_is_healthy, self.root, "root+v")
        d.addCallback(lambda ign:
                      self.web_json(self.mutable, t="check", verify="true"))
        d.addCallback(self.json_check_is_healthy, self.mutable, "mutable+v")
        d.addCallback(lambda ign:
                      self.web_json(self.large, t="check", verify="true"))
        d.addCallback(self.json_check_is_healthy, self.large, "large+v",
                      incomplete=True)
        d.addCallback(lambda ign:
                      self.web_json(self.small, t="check", verify="true"))
        d.addCallback(self.json_check_lit, self.small, "small+v")
        d.addCallback(lambda ign:
                      self.web_json(self.small2, t="check", verify="true"))
        d.addCallback(self.json_check_lit, self.small2, "small2+v")
        d.addCallback(lambda ign: self.web_json(self.empty_lit_dir, t="check", verify="true"))
        d.addCallback(self.json_check_lit, self.empty_lit_dir, "empty_lit_dir+v")
        d.addCallback(lambda ign: self.web_json(self.tiny_lit_dir, t="check", verify="true"))
        d.addCallback(self.json_check_lit, self.tiny_lit_dir, "tiny_lit_dir+v")

        # check and repair, no verify
        d.addCallback(lambda ign:
                      self.web_json(self.root, t="check", repair="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.root, "root+r")
        d.addCallback(lambda ign:
                      self.web_json(self.mutable, t="check", repair="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.mutable, "mutable+r")
        d.addCallback(lambda ign:
                      self.web_json(self.large, t="check", repair="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.large, "large+r")
        d.addCallback(lambda ign:
                      self.web_json(self.small, t="check", repair="true"))
        d.addCallback(self.json_check_lit, self.small, "small+r")
        d.addCallback(lambda ign:
                      self.web_json(self.small2, t="check", repair="true"))
        d.addCallback(self.json_check_lit, self.small2, "small2+r")
        d.addCallback(lambda ign: self.web_json(self.empty_lit_dir, t="check", repair="true"))
        d.addCallback(self.json_check_lit, self.empty_lit_dir, "empty_lit_dir+r")
        d.addCallback(lambda ign: self.web_json(self.tiny_lit_dir, t="check", repair="true"))
        d.addCallback(self.json_check_lit, self.tiny_lit_dir, "tiny_lit_dir+r")

        # check+verify+repair
        d.addCallback(lambda ign:
                      self.web_json(self.root, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.root, "root+vr")
        d.addCallback(lambda ign:
                      self.web_json(self.mutable, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.mutable, "mutable+vr")
        d.addCallback(lambda ign:
                      self.web_json(self.large, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_and_repair_is_healthy, self.large, "large+vr", incomplete=True)
        d.addCallback(lambda ign:
                      self.web_json(self.small, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_lit, self.small, "small+vr")
        d.addCallback(lambda ign:
                      self.web_json(self.small2, t="check", repair="true", verify="true"))
        d.addCallback(self.json_check_lit, self.small2, "small2+vr")
        d.addCallback(lambda ign: self.web_json(self.empty_lit_dir, t="check", repair="true", verify=True))
        d.addCallback(self.json_check_lit, self.empty_lit_dir, "empty_lit_dir+vr")
        d.addCallback(lambda ign: self.web_json(self.tiny_lit_dir, t="check", repair="true", verify=True))
        d.addCallback(self.json_check_lit, self.tiny_lit_dir, "tiny_lit_dir+vr")

        # now run a deep-check, with various verify= and repair= flags
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", output="json"))
        d.addCallback(self.json_full_deepcheck_is_healthy, self.root, "root+d")
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", verify="true",
                                    output="json"))
        d.addCallback(self.json_full_deepcheck_is_healthy, self.root, "root+dv")
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", repair="true",
                                    output="json"))
        d.addCallback(self.json_full_deepcheck_and_repair_is_healthy, self.root, "root+dr")
        d.addCallback(lambda ign:
                      self.slow_web(self.root, t="start-deep-check", verify="true", repair="true", output="json"))
        d.addCallback(self.json_full_deepcheck_and_repair_is_healthy, self.root, "root+dvr")

        # now look at t=info
        d.addCallback(lambda ign: self.web(self.root, t="info"))
        # TODO: examine the output
        d.addCallback(lambda ign: self.web(self.mutable, t="info"))
        d.addCallback(lambda ign: self.web(self.large, t="info"))
        d.addCallback(lambda ign: self.web(self.small, t="info"))
        d.addCallback(lambda ign: self.web(self.small2, t="info"))
        d.addCallback(lambda ign: self.web(self.empty_lit_dir, t="info"))
        d.addCallback(lambda ign: self.web(self.tiny_lit_dir, t="info"))

        return d

    def do_test_cli_good(self, ignored):
        d = defer.succeed(None)
        d.addCallback(lambda ign: self.do_cli_manifest_stream1())
        d.addCallback(lambda ign: self.do_cli_manifest_stream2())
        d.addCallback(lambda ign: self.do_cli_manifest_stream3())
        d.addCallback(lambda ign: self.do_cli_manifest_stream4())
        d.addCallback(lambda ign: self.do_cli_manifest_stream5())
        d.addCallback(lambda ign: self.do_cli_stats1())
        d.addCallback(lambda ign: self.do_cli_stats2())
        return d

    def _check_manifest_storage_index(self, out):
        lines = [l for l in out.split("\n") if l]
        self.failUnlessEqual(len(lines), 3)
        self.failUnless(base32.b2a(self.root.get_storage_index()) in lines)
        self.failUnless(base32.b2a(self.mutable.get_storage_index()) in lines)
        self.failUnless(base32.b2a(self.large.get_storage_index()) in lines)

    def do_cli_manifest_stream1(self):
        d = self.do_cli("manifest", self.root_uri)
        def _check(args):
            (rc, out, err) = args
            self.failUnlessEqual(err, "")
            lines = [l for l in out.split("\n") if l]
            self.failUnlessEqual(len(lines), 8)
            caps = {}
            for l in lines:
                try:
                    cap, path = l.split(None, 1)
                except ValueError:
                    cap = l.strip()
                    path = ""
                caps[cap] = path
            self.failUnless(self.root.get_uri() in caps)
            self.failUnlessEqual(caps[self.root.get_uri()], "")
            self.failUnlessEqual(caps[self.mutable.get_uri()], "mutable")
            self.failUnlessEqual(caps[self.large.get_uri()], "large")
            self.failUnlessEqual(caps[self.small.get_uri()], "small")
            self.failUnlessEqual(caps[self.small2.get_uri()], "small2")
            self.failUnlessEqual(caps[self.empty_lit_dir.get_uri()], "empty_lit_dir")
            self.failUnlessEqual(caps[self.tiny_lit_dir.get_uri()], "tiny_lit_dir")
        d.addCallback(_check)
        return d

    def do_cli_manifest_stream2(self):
        d = self.do_cli("manifest", "--raw", self.root_uri)
        def _check(args):
            (rc, out, err) = args
            self.failUnlessEqual(err, "")
            # this should be the same as the POST t=stream-manifest output
            self._check_streamed_manifest(out)
        d.addCallback(_check)
        return d

    def do_cli_manifest_stream3(self):
        d = self.do_cli("manifest", "--storage-index", self.root_uri)
        def _check(args):
            (rc, out, err) = args
            self.failUnlessEqual(err, "")
            self._check_manifest_storage_index(out)
        d.addCallback(_check)
        return d

    def do_cli_manifest_stream4(self):
        d = self.do_cli("manifest", "--verify-cap", self.root_uri)
        def _check(args):
            (rc, out, err) = args
            self.failUnlessEqual(err, "")
            lines = [l for l in out.split("\n") if l]
            self.failUnlessEqual(len(lines), 3)
            self.failUnless(self.root.get_verify_cap().to_string() in lines)
            self.failUnless(self.mutable.get_verify_cap().to_string() in lines)
            self.failUnless(self.large.get_verify_cap().to_string() in lines)
        d.addCallback(_check)
        return d

    def do_cli_manifest_stream5(self):
        d = self.do_cli("manifest", "--repair-cap", self.root_uri)
        def _check(args):
            (rc, out, err) = args
            self.failUnlessEqual(err, "")
            lines = [l for l in out.split("\n") if l]
            self.failUnlessEqual(len(lines), 3)
            self.failUnless(self.root.get_repair_cap().to_string() in lines)
            self.failUnless(self.mutable.get_repair_cap().to_string() in lines)
            self.failUnless(self.large.get_repair_cap().to_string() in lines)
        d.addCallback(_check)
        return d

    def do_cli_stats1(self):
        d = self.do_cli("stats", self.root_uri)
        def _check3(args):
            (rc, out, err) = args
            lines = [l.strip() for l in out.split("\n") if l]
            self.failUnless("count-immutable-files: 1" in lines)
            self.failUnless("count-mutable-files: 1" in lines)
            self.failUnless("count-literal-files: 3" in lines)
            self.failUnless("count-files: 5" in lines)
            self.failUnless("count-directories: 3" in lines)
            self.failUnless("size-immutable-files: 13000    (13.00 kB, 12.70 kiB)" in lines, lines)
            self.failUnless("size-literal-files: 56" in lines, lines)
            self.failUnless("    4-10    : 1    (10 B, 10 B)".strip() in lines, lines)
            self.failUnless("   11-31    : 2    (31 B, 31 B)".strip() in lines, lines)
            self.failUnless("10001-31622 : 1    (31.62 kB, 30.88 kiB)".strip() in lines, lines)
        d.addCallback(_check3)
        return d

    def do_cli_stats2(self):
        d = self.do_cli("stats", "--raw", self.root_uri)
        def _check4(args):
            (rc, out, err) = args
            data = json.loads(out)
            self.failUnlessEqual(data["count-immutable-files"], 1)
            self.failUnlessEqual(data["count-immutable-files"], 1)
            self.failUnlessEqual(data["count-mutable-files"], 1)
            self.failUnlessEqual(data["count-literal-files"], 3)
            self.failUnlessEqual(data["count-files"], 5)
            self.failUnlessEqual(data["count-directories"], 3)
            self.failUnlessEqual(data["size-immutable-files"], 13000)
            self.failUnlessEqual(data["size-literal-files"], 56)
            self.failUnless([4,10,1] in data["size-files-histogram"])
            self.failUnless([11,31,2] in data["size-files-histogram"])
            self.failUnless([10001,31622,1] in data["size-files-histogram"])
        d.addCallback(_check4)
        return d


class DeepCheckWebBad(DeepCheckBase, unittest.TestCase):
    def test_bad(self):
        self.basedir = "deepcheck/DeepCheckWebBad/bad"
        self.set_up_grid()
        d = self.set_up_damaged_tree()
        d.addCallback(self.do_check)
        d.addCallback(self.do_deepcheck)
        d.addCallback(self.do_deepcheck_broken)
        d.addCallback(self.do_test_web_bad)
        d.addErrback(self.explain_web_error)
        d.addErrback(self.explain_error)
        return d



    def set_up_damaged_tree(self):
        # 6.4s

        # root
        #   mutable-good
        #   mutable-missing-shares
        #   mutable-corrupt-shares
        #   mutable-unrecoverable
        #   large-good
        #   large-missing-shares
        #   large-corrupt-shares
        #   large-unrecoverable
        # broken
        #   large1-good
        #   subdir-good
        #     large2-good
        #   subdir-unrecoverable
        #     large3-good

        self.nodes = {}

        c0 = self.g.clients[0]
        d = c0.create_dirnode()
        def _created_root(n):
            self.root = n
            self.root_uri = n.get_uri()
        d.addCallback(_created_root)
        d.addCallback(self.create_mangled, "mutable-good")
        d.addCallback(self.create_mangled, "mutable-missing-shares")
        d.addCallback(self.create_mangled, "mutable-corrupt-shares")
        d.addCallback(self.create_mangled, "mutable-unrecoverable")
        d.addCallback(self.create_mangled, "large-good")
        d.addCallback(self.create_mangled, "large-missing-shares")
        d.addCallback(self.create_mangled, "large-corrupt-shares")
        d.addCallback(self.create_mangled, "large-unrecoverable")
        d.addCallback(lambda ignored: c0.create_dirnode())
        d.addCallback(self._stash_node, "broken")
        large1 = upload.Data("Lots of data\n" * 1000 + "large1" + "\n", None)
        d.addCallback(lambda ignored:
                      self.nodes["broken"].add_file(u"large1", large1))
        d.addCallback(lambda ignored:
                      self.nodes["broken"].create_subdirectory(u"subdir-good"))
        large2 = upload.Data("Lots of data\n" * 1000 + "large2" + "\n", None)
        d.addCallback(lambda subdir: subdir.add_file(u"large2-good", large2))
        d.addCallback(lambda ignored:
                      self.nodes["broken"].create_subdirectory(u"subdir-unrecoverable"))
        d.addCallback(self._stash_node, "subdir-unrecoverable")
        large3 = upload.Data("Lots of data\n" * 1000 + "large3" + "\n", None)
        d.addCallback(lambda subdir: subdir.add_file(u"large3-good", large3))
        d.addCallback(lambda ignored:
                      self._delete_most_shares(self.nodes["broken"]))
        return d

    def _stash_node(self, node, name):
        self.nodes[name] = node
        return node

    def create_mangled(self, ignored, name):
        nodetype, mangletype = name.split("-", 1)
        if nodetype == "mutable":
            mutable_uploadable = MutableData("mutable file contents")
            d = self.g.clients[0].create_mutable_file(mutable_uploadable)
            d.addCallback(lambda n: self.root.set_node(unicode(name), n))
        elif nodetype == "large":
            large = upload.Data("Lots of data\n" * 1000 + name + "\n", None)
            d = self.root.add_file(unicode(name), large)
        elif nodetype == "small":
            small = upload.Data("Small enough for a LIT", None)
            d = self.root.add_file(unicode(name), small)

        d.addCallback(self._stash_node, name)

        if mangletype == "good":
            pass
        elif mangletype == "missing-shares":
            d.addCallback(self._delete_some_shares)
        elif mangletype == "corrupt-shares":
            d.addCallback(self._corrupt_some_shares)
        else:
            assert mangletype == "unrecoverable"
            d.addCallback(self._delete_most_shares)

        return d

    def _delete_some_shares(self, node):
        self.delete_shares_numbered(node.get_uri(), [0,1])

    @defer.inlineCallbacks
    def _corrupt_some_shares(self, node):
        for (shnum, serverid, sharefile) in self.find_uri_shares(node.get_uri()):
            if shnum in (0,1):
                yield run_cli("debug", "corrupt-share", sharefile)

    def _delete_most_shares(self, node):
        self.delete_shares_numbered(node.get_uri(), range(1,10))


    def check_is_healthy(self, cr, where):
        try:
            self.failUnless(ICheckResults.providedBy(cr), (cr, type(cr), where))
            self.failUnless(cr.is_healthy(), (cr.get_report(), cr.is_healthy(), cr.get_summary(), where))
            self.failUnless(cr.is_recoverable(), where)
            self.failUnlessEqual(cr.get_version_counter_recoverable(), 1, where)
            self.failUnlessEqual(cr.get_version_counter_unrecoverable(), 0, where)
            return cr
        except Exception as le:
            le.args = tuple(le.args + (where,))
            raise

    def check_is_missing_shares(self, cr, where):
        self.failUnless(ICheckResults.providedBy(cr), where)
        self.failIf(cr.is_healthy(), where)
        self.failUnless(cr.is_recoverable(), where)
        self.failUnlessEqual(cr.get_version_counter_recoverable(), 1, where)
        self.failUnlessEqual(cr.get_version_counter_unrecoverable(), 0, where)
        return cr

    def check_has_corrupt_shares(self, cr, where):
        # by "corrupt-shares" we mean the file is still recoverable
        self.failUnless(ICheckResults.providedBy(cr), where)
        self.failIf(cr.is_healthy(), (where, cr))
        self.failUnless(cr.is_recoverable(), where)
        self.failUnless(cr.get_share_counter_good() < 10, where)
        self.failUnless(cr.get_corrupt_shares(), where)
        return cr

    def check_is_unrecoverable(self, cr, where):
        self.failUnless(ICheckResults.providedBy(cr), where)
        self.failIf(cr.is_healthy(), where)
        self.failIf(cr.is_recoverable(), where)
        self.failUnless(cr.get_share_counter_good() < cr.get_encoding_needed(),
                        (cr.get_share_counter_good(), cr.get_encoding_needed(),
                         where))
        self.failUnlessEqual(cr.get_version_counter_recoverable(), 0, where)
        self.failUnlessEqual(cr.get_version_counter_unrecoverable(), 1, where)
        return cr

    def do_check(self, ignored):
        d = defer.succeed(None)

        # check the individual items, without verification. This will not
        # detect corrupt shares.
        def _check(which, checker):
            d = self.nodes[which].check(Monitor())
            d.addCallback(checker, which + "--check")
            return d

        d.addCallback(lambda ign: _check("mutable-good", self.check_is_healthy))
        d.addCallback(lambda ign: _check("mutable-missing-shares",
                                         self.check_is_missing_shares))
        d.addCallback(lambda ign: _check("mutable-corrupt-shares",
                                         self.check_is_healthy))
        d.addCallback(lambda ign: _check("mutable-unrecoverable",
                                         self.check_is_unrecoverable))
        d.addCallback(lambda ign: _check("large-good", self.check_is_healthy))
        d.addCallback(lambda ign: _check("large-missing-shares",
                                         self.check_is_missing_shares))
        d.addCallback(lambda ign: _check("large-corrupt-shares",
                                         self.check_is_healthy))
        d.addCallback(lambda ign: _check("large-unrecoverable",
                                         self.check_is_unrecoverable))

        # and again with verify=True, which *does* detect corrupt shares.
        def _checkv(which, checker):
            d = self.nodes[which].check(Monitor(), verify=True)
            d.addCallback(checker, which + "--check-and-verify")
            return d

        d.addCallback(lambda ign: _checkv("mutable-good", self.check_is_healthy))
        d.addCallback(lambda ign: _checkv("mutable-missing-shares",
                                         self.check_is_missing_shares))
        d.addCallback(lambda ign: _checkv("mutable-corrupt-shares",
                                         self.check_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("mutable-unrecoverable",
                                         self.check_is_unrecoverable))
        d.addCallback(lambda ign: _checkv("large-good", self.check_is_healthy))
        d.addCallback(lambda ign: _checkv("large-missing-shares", self.check_is_missing_shares))
        d.addCallback(lambda ign: _checkv("large-corrupt-shares", self.check_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("large-unrecoverable",
                                         self.check_is_unrecoverable))

        return d

    def do_deepcheck(self, ignored):
        d = defer.succeed(None)

        # now deep-check the root, with various verify= and repair= options
        d.addCallback(lambda ign:
                      self.root.start_deep_check().when_done())
        def _check1(cr):
            self.failUnless(IDeepCheckResults.providedBy(cr))
            c = cr.get_counters()
            self.failUnlessEqual(c["count-objects-checked"], 9)
            self.failUnlessEqual(c["count-objects-healthy"], 5)
            self.failUnlessEqual(c["count-objects-unhealthy"], 4)
            self.failUnlessEqual(c["count-objects-unrecoverable"], 2)
        d.addCallback(_check1)

        d.addCallback(lambda ign:
                      self.root.start_deep_check(verify=True).when_done())
        def _check2(cr):
            self.failUnless(IDeepCheckResults.providedBy(cr))
            c = cr.get_counters()
            self.failUnlessEqual(c["count-objects-checked"], 9)
            self.failUnlessEqual(c["count-objects-healthy"], 3)
            self.failUnlessEqual(c["count-objects-unhealthy"], 6)
            self.failUnlessEqual(c["count-objects-healthy"], 3) # root, mutable good, large good
            self.failUnlessEqual(c["count-objects-unrecoverable"], 2) # mutable unrecoverable, large unrecoverable
        d.addCallback(_check2)

        return d

    def do_deepcheck_broken(self, ignored):
        # deep-check on the broken directory should fail, because of the
        # untraversable subdir
        def _do_deep_check():
            return self.nodes["broken"].start_deep_check().when_done()
        d = self.shouldFail(UnrecoverableFileError, "do_deep_check",
                            "no recoverable versions",
                            _do_deep_check)
        return d

    def json_is_healthy(self, data, where):
        r = data["results"]
        self.failUnless(r["healthy"], where)
        self.failUnless(r["recoverable"], where)
        self.failUnlessEqual(r["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 0, where)

    def json_is_missing_shares(self, data, where):
        r = data["results"]
        self.failIf(r["healthy"], where)
        self.failUnless(r["recoverable"], where)
        self.failUnlessEqual(r["count-recoverable-versions"], 1, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 0, where)

    def json_has_corrupt_shares(self, data, where):
        # by "corrupt-shares" we mean the file is still recoverable
        r = data["results"]
        self.failIf(r["healthy"], where)
        self.failUnless(r["recoverable"], where)
        self.failUnless(r["count-shares-good"] < 10, where)
        self.failUnless(r["count-corrupt-shares"], where)
        self.failUnless(r["list-corrupt-shares"], where)

    def json_is_unrecoverable(self, data, where):
        r = data["results"]
        self.failIf(r["healthy"], where)
        self.failIf(r["recoverable"], where)
        self.failUnless(r["count-shares-good"] < r["count-shares-needed"],
                        where)
        self.failUnlessEqual(r["count-recoverable-versions"], 0, where)
        self.failUnlessEqual(r["count-unrecoverable-versions"], 1, where)

    def do_test_web_bad(self, ignored):
        d = defer.succeed(None)

        # check, no verify
        def _check(which, checker):
            d = self.web_json(self.nodes[which], t="check")
            d.addCallback(checker, which + "--webcheck")
            return d

        d.addCallback(lambda ign: _check("mutable-good",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("mutable-missing-shares",
                                         self.json_is_missing_shares))
        d.addCallback(lambda ign: _check("mutable-corrupt-shares",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("mutable-unrecoverable",
                                         self.json_is_unrecoverable))
        d.addCallback(lambda ign: _check("large-good",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("large-missing-shares",
                                         self.json_is_missing_shares))
        d.addCallback(lambda ign: _check("large-corrupt-shares",
                                         self.json_is_healthy))
        d.addCallback(lambda ign: _check("large-unrecoverable",
                                         self.json_is_unrecoverable))

        # check and verify
        def _checkv(which, checker):
            d = self.web_json(self.nodes[which], t="check", verify="true")
            d.addCallback(checker, which + "--webcheck-and-verify")
            return d

        d.addCallback(lambda ign: _checkv("mutable-good",
                                          self.json_is_healthy))
        d.addCallback(lambda ign: _checkv("mutable-missing-shares",
                                         self.json_is_missing_shares))
        d.addCallback(lambda ign: _checkv("mutable-corrupt-shares",
                                         self.json_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("mutable-unrecoverable",
                                         self.json_is_unrecoverable))
        d.addCallback(lambda ign: _checkv("large-good",
                                          self.json_is_healthy))
        d.addCallback(lambda ign: _checkv("large-missing-shares", self.json_is_missing_shares))
        d.addCallback(lambda ign: _checkv("large-corrupt-shares", self.json_has_corrupt_shares))
        d.addCallback(lambda ign: _checkv("large-unrecoverable",
                                         self.json_is_unrecoverable))

        return d

class Large(DeepCheckBase, unittest.TestCase):
    def test_lots_of_lits(self):
        self.basedir = "deepcheck/Large/lots_of_lits"
        self.set_up_grid()
        # create the following directory structure:
        #  root/
        #   subdir/
        #    000-large (CHK)
        #    001-small (LIT)
        #    002-small
        #    ...
        #    399-small
        # then do a deepcheck and make sure it doesn't cause a
        # Deferred-tail-recursion stack overflow

        COUNT = 400
        c0 = self.g.clients[0]
        d = c0.create_dirnode()
        self.stash = {}
        def _created_root(n):
            self.root = n
            return n
        d.addCallback(_created_root)
        d.addCallback(lambda root: root.create_subdirectory(u"subdir"))
        def _add_children(subdir_node):
            self.subdir_node = subdir_node
            kids = {}
            for i in range(1, COUNT):
                litcap = LiteralFileURI("%03d-data" % i).to_string()
                kids[u"%03d-small" % i] = (litcap, litcap)
            return subdir_node.set_children(kids)
        d.addCallback(_add_children)
        up = upload.Data("large enough for CHK" * 100, "")
        d.addCallback(lambda ign: self.subdir_node.add_file(u"0000-large", up))

        def _start_deepcheck(ignored):
            return self.web(self.root, method="POST", t="stream-deep-check")
        d.addCallback(_start_deepcheck)
        def _check(output_and_url):
            (output, url) = output_and_url
            units = list(self.parse_streamed_json(output))
            self.failUnlessEqual(len(units), 2+COUNT+1)
        d.addCallback(_check)

        return d
