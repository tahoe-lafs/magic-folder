"""Tests for the dirnode module."""
import six
import time
import unicodedata
from zope.interface import implementer
from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet.interfaces import IConsumer
from allmydata import uri, dirnode
from allmydata.client import _Client
from allmydata.immutable import upload
from allmydata.interfaces import IImmutableFileNode, IMutableFileNode, \
     ExistingChildError, NoSuchChildError, MustNotBeUnknownRWError, \
     MustBeDeepImmutableError, MustBeReadonlyError, \
     IDeepCheckResults, IDeepCheckAndRepairResults, \
     MDMF_VERSION, SDMF_VERSION
from allmydata.mutable.filenode import MutableFileNode
from allmydata.mutable.common import UncoordinatedWriteError
from allmydata.util import hashutil, base32
from allmydata.util.netstring import split_netstring
from allmydata.monitor import Monitor
from allmydata.test.common import make_chk_file_uri, make_mutable_file_uri, \
     ErrorMixin
from allmydata.test.no_network import GridTestMixin
from allmydata.unknown import UnknownNode, strip_prefix_for_ro
from allmydata.nodemaker import NodeMaker
from base64 import b32decode
import allmydata.test.common_util as testutil

if six.PY3:
    long = int


@implementer(IConsumer)
class MemAccum(object):
    def registerProducer(self, producer, streaming):
        self.producer = producer
        self.producer.resumeProducing()
        pass
    def unregisterProducer(self):
        pass
    def write(self, data):
        assert not hasattr(self, 'data')
        self.data = data
        self.producer.resumeProducing()

setup_py_uri = "URI:CHK:n7r3m6wmomelk4sep3kw5cvduq:os7ijw5c3maek7pg65e5254k2fzjflavtpejjyhshpsxuqzhcwwq:3:20:14861"
one_uri = "URI:LIT:n5xgk" # LIT for "one"
mut_write_uri = "URI:SSK:vfvcbdfbszyrsaxchgevhmmlii:euw4iw7bbnkrrwpzuburbhppuxhc3gwxv26f6imekhz7zyw2ojnq"
mdmf_write_uri = "URI:MDMF:x533rhbm6kiehzl5kj3s44n5ie:4gif5rhneyd763ouo5qjrgnsoa3bg43xycy4robj2rf3tvmhdl3a"
empty_litdir_uri = "URI:DIR2-LIT:"
tiny_litdir_uri = "URI:DIR2-LIT:gqytunj2onug64tufqzdcosvkjetutcjkq5gw4tvm5vwszdgnz5hgyzufqydulbshj5x2lbm" # contains one child which is itself also LIT
mut_read_uri = "URI:SSK-RO:jf6wkflosyvntwxqcdo7a54jvm:euw4iw7bbnkrrwpzuburbhppuxhc3gwxv26f6imekhz7zyw2ojnq"
mdmf_read_uri = "URI:MDMF-RO:d4cydxselputycfzkw6qgz4zv4:4gif5rhneyd763ouo5qjrgnsoa3bg43xycy4robj2rf3tvmhdl3a"
future_write_uri = "x-tahoe-crazy://I_am_from_the_future."
future_read_uri = "x-tahoe-crazy-readonly://I_am_from_the_future."
future_nonascii_write_uri = u"x-tahoe-even-more-crazy://I_am_from_the_future_rw_\u263A".encode('utf-8')
future_nonascii_read_uri = u"x-tahoe-even-more-crazy-readonly://I_am_from_the_future_ro_\u263A".encode('utf-8')

# 'o' 'n' 'e-macron'
one_nfc = u"on\u0113"
one_nfd = u"one\u0304"

class Dirnode(GridTestMixin, unittest.TestCase,
              testutil.ReallyEqualMixin, testutil.ShouldFailMixin, testutil.StallMixin, ErrorMixin):

    def _do_create_test(self, mdmf=False):
        c = self.g.clients[0]

        self.expected_manifest = []
        self.expected_verifycaps = set()
        self.expected_storage_indexes = set()

        d = None
        if mdmf:
            d = c.create_dirnode(version=MDMF_VERSION)
        else:
            d = c.create_dirnode()
        def _then(n):
            # /
            self.rootnode = n
            backing_node = n._node
            if mdmf:
                self.failUnlessEqual(backing_node.get_version(),
                                     MDMF_VERSION)
            else:
                self.failUnlessEqual(backing_node.get_version(),
                                     SDMF_VERSION)
            self.failUnless(n.is_mutable())
            u = n.get_uri()
            self.failUnless(u)
            cap_formats = []
            if mdmf:
                cap_formats = ["URI:DIR2-MDMF:",
                               "URI:DIR2-MDMF-RO:",
                               "URI:DIR2-MDMF-Verifier:"]
            else:
                cap_formats = ["URI:DIR2:",
                               "URI:DIR2-RO",
                               "URI:DIR2-Verifier:"]
            rw, ro, v = cap_formats
            self.failUnless(u.startswith(rw), u)
            u_ro = n.get_readonly_uri()
            self.failUnless(u_ro.startswith(ro), u_ro)
            u_v = n.get_verify_cap().to_string()
            self.failUnless(u_v.startswith(v), u_v)
            u_r = n.get_repair_cap().to_string()
            self.failUnlessReallyEqual(u_r, u)
            self.expected_manifest.append( ((), u) )
            self.expected_verifycaps.add(u_v)
            si = n.get_storage_index()
            self.expected_storage_indexes.add(base32.b2a(si))
            expected_si = n._uri.get_storage_index()
            self.failUnlessReallyEqual(si, expected_si)

            d = n.list()
            d.addCallback(lambda res: self.failUnlessEqual(res, {}))
            d.addCallback(lambda res: n.has_child(u"missing"))
            d.addCallback(lambda res: self.failIf(res))

            fake_file_uri = make_mutable_file_uri()
            other_file_uri = make_mutable_file_uri()
            m = c.nodemaker.create_from_cap(fake_file_uri)
            ffu_v = m.get_verify_cap().to_string()
            self.expected_manifest.append( ((u"child",) , m.get_uri()) )
            self.expected_verifycaps.add(ffu_v)
            self.expected_storage_indexes.add(base32.b2a(m.get_storage_index()))
            d.addCallback(lambda res: n.set_uri(u"child",
                                                fake_file_uri, fake_file_uri))
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "set_uri-no",
                                          "child 'child' already exists",
                                          n.set_uri, u"child",
                                          other_file_uri, other_file_uri,
                                          overwrite=False))
            # /
            # /child = mutable

            d.addCallback(lambda res: n.create_subdirectory(u"subdir"))

            # /
            # /child = mutable
            # /subdir = directory
            def _created(subdir):
                self.failUnless(isinstance(subdir, dirnode.DirectoryNode))
                self.subdir = subdir
                new_v = subdir.get_verify_cap().to_string()
                assert isinstance(new_v, str)
                self.expected_manifest.append( ((u"subdir",), subdir.get_uri()) )
                self.expected_verifycaps.add(new_v)
                si = subdir.get_storage_index()
                self.expected_storage_indexes.add(base32.b2a(si))
            d.addCallback(_created)

            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "mkdir-no",
                                          "child 'subdir' already exists",
                                          n.create_subdirectory, u"subdir",
                                          overwrite=False))

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(set(children.keys()),
                                                     set([u"child", u"subdir"])))

            d.addCallback(lambda res: n.start_deep_stats().when_done())
            def _check_deepstats(stats):
                self.failUnless(isinstance(stats, dict))
                expected = {"count-immutable-files": 0,
                            "count-mutable-files": 1,
                            "count-literal-files": 0,
                            "count-files": 1,
                            "count-directories": 2,
                            "size-immutable-files": 0,
                            "size-literal-files": 0,
                            #"size-directories": 616, # varies
                            #"largest-directory": 616,
                            "largest-directory-children": 2,
                            "largest-immutable-file": 0,
                            }
                for k,v in expected.iteritems():
                    self.failUnlessReallyEqual(stats[k], v,
                                               "stats[%s] was %s, not %s" %
                                               (k, stats[k], v))
                self.failUnless(stats["size-directories"] > 500,
                                stats["size-directories"])
                self.failUnless(stats["largest-directory"] > 500,
                                stats["largest-directory"])
                self.failUnlessReallyEqual(stats["size-files-histogram"], [])
            d.addCallback(_check_deepstats)

            d.addCallback(lambda res: n.build_manifest().when_done())
            def _check_manifest(res):
                manifest = res["manifest"]
                self.failUnlessReallyEqual(sorted(manifest),
                                           sorted(self.expected_manifest))
                stats = res["stats"]
                _check_deepstats(stats)
                self.failUnlessReallyEqual(self.expected_verifycaps,
                                           res["verifycaps"])
                self.failUnlessReallyEqual(self.expected_storage_indexes,
                                           res["storage-index"])
            d.addCallback(_check_manifest)

            def _add_subsubdir(res):
                return self.subdir.create_subdirectory(u"subsubdir")
            d.addCallback(_add_subsubdir)
            # /
            # /child = mutable
            # /subdir = directory
            # /subdir/subsubdir = directory
            d.addCallback(lambda res: n.get_child_at_path(u"subdir/subsubdir"))
            d.addCallback(lambda subsubdir:
                          self.failUnless(isinstance(subsubdir,
                                                     dirnode.DirectoryNode)))
            d.addCallback(lambda res: n.get_child_at_path(u""))
            d.addCallback(lambda res: self.failUnlessReallyEqual(res.get_uri(),
                                                                 n.get_uri()))

            d.addCallback(lambda res: n.get_metadata_for(u"child"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()),
                                               set(["tahoe"])))

            d.addCallback(lambda res:
                          self.shouldFail(NoSuchChildError, "gcamap-no",
                                          "nope",
                                          n.get_child_and_metadata_at_path,
                                          u"subdir/nope"))
            d.addCallback(lambda res:
                          n.get_child_and_metadata_at_path(u""))
            def _check_child_and_metadata1(res):
                child, metadata = res
                self.failUnless(isinstance(child, dirnode.DirectoryNode))
                # edge-metadata needs at least one path segment
                self.failUnlessEqual(set(metadata.keys()), set([]))
            d.addCallback(_check_child_and_metadata1)
            d.addCallback(lambda res:
                          n.get_child_and_metadata_at_path(u"child"))

            def _check_child_and_metadata2(res):
                child, metadata = res
                self.failUnlessReallyEqual(child.get_uri(),
                                           fake_file_uri)
                self.failUnlessEqual(set(metadata.keys()), set(["tahoe"]))
            d.addCallback(_check_child_and_metadata2)

            d.addCallback(lambda res:
                          n.get_child_and_metadata_at_path(u"subdir/subsubdir"))
            def _check_child_and_metadata3(res):
                child, metadata = res
                self.failUnless(isinstance(child, dirnode.DirectoryNode))
                self.failUnlessEqual(set(metadata.keys()), set(["tahoe"]))
            d.addCallback(_check_child_and_metadata3)

            # set_uri + metadata
            # it should be possible to add a child without any metadata
            d.addCallback(lambda res: n.set_uri(u"c2",
                                                fake_file_uri, fake_file_uri,
                                                {}))
            d.addCallback(lambda res: n.get_metadata_for(u"c2"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))

            # You can't override the link timestamps.
            d.addCallback(lambda res: n.set_uri(u"c2",
                                                fake_file_uri, fake_file_uri,
                                                { 'tahoe': {'linkcrtime': "bogus"}}))
            d.addCallback(lambda res: n.get_metadata_for(u"c2"))
            def _has_good_linkcrtime(metadata):
                self.failUnless(metadata.has_key('tahoe'))
                self.failUnless(metadata['tahoe'].has_key('linkcrtime'))
                self.failIfEqual(metadata['tahoe']['linkcrtime'], 'bogus')
            d.addCallback(_has_good_linkcrtime)

            # if we don't set any defaults, the child should get timestamps
            d.addCallback(lambda res: n.set_uri(u"c3",
                                                fake_file_uri, fake_file_uri))
            d.addCallback(lambda res: n.get_metadata_for(u"c3"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))

            # we can also add specific metadata at set_uri() time
            d.addCallback(lambda res: n.set_uri(u"c4",
                                                fake_file_uri, fake_file_uri,
                                                {"key": "value"}))
            d.addCallback(lambda res: n.get_metadata_for(u"c4"))
            d.addCallback(lambda metadata:
                              self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                              (metadata['key'] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"c2"))
            d.addCallback(lambda res: n.delete(u"c3"))
            d.addCallback(lambda res: n.delete(u"c4"))

            # set_node + metadata
            # it should be possible to add a child without any metadata except for timestamps
            d.addCallback(lambda res: n.set_node(u"d2", n, {}))
            d.addCallback(lambda res: c.create_dirnode())
            d.addCallback(lambda n2:
                          self.shouldFail(ExistingChildError, "set_node-no",
                                          "child 'd2' already exists",
                                          n.set_node, u"d2", n2,
                                          overwrite=False))
            d.addCallback(lambda res: n.get_metadata_for(u"d2"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))

            # if we don't set any defaults, the child should get timestamps
            d.addCallback(lambda res: n.set_node(u"d3", n))
            d.addCallback(lambda res: n.get_metadata_for(u"d3"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))

            # we can also add specific metadata at set_node() time
            d.addCallback(lambda res: n.set_node(u"d4", n,
                                                {"key": "value"}))
            d.addCallback(lambda res: n.get_metadata_for(u"d4"))
            d.addCallback(lambda metadata:
                          self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                          (metadata["key"] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"d2"))
            d.addCallback(lambda res: n.delete(u"d3"))
            d.addCallback(lambda res: n.delete(u"d4"))

            # metadata through set_children()
            d.addCallback(lambda res:
                          n.set_children({
                              u"e1": (fake_file_uri, fake_file_uri),
                              u"e2": (fake_file_uri, fake_file_uri, {}),
                              u"e3": (fake_file_uri, fake_file_uri,
                                      {"key": "value"}),
                              }))
            d.addCallback(lambda n2: self.failUnlessIdentical(n2, n))
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "set_children-no",
                                          "child 'e1' already exists",
                                          n.set_children,
                                          { u"e1": (other_file_uri,
                                                    other_file_uri),
                                            u"new": (other_file_uri,
                                                     other_file_uri),
                                            },
                                          overwrite=False))
            # and 'new' should not have been created
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children: self.failIf(u"new" in children))
            d.addCallback(lambda res: n.get_metadata_for(u"e1"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))
            d.addCallback(lambda res: n.get_metadata_for(u"e2"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))
            d.addCallback(lambda res: n.get_metadata_for(u"e3"))
            d.addCallback(lambda metadata:
                          self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                          (metadata["key"] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"e1"))
            d.addCallback(lambda res: n.delete(u"e2"))
            d.addCallback(lambda res: n.delete(u"e3"))

            # metadata through set_nodes()
            d.addCallback(lambda res:
                          n.set_nodes({ u"f1": (n, None),
                                        u"f2": (n, {}),
                                        u"f3": (n, {"key": "value"}),
                                        }))
            d.addCallback(lambda n2: self.failUnlessIdentical(n2, n))
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "set_nodes-no",
                                          "child 'f1' already exists",
                                          n.set_nodes, { u"f1": (n, None),
                                                         u"new": (n, None), },
                                          overwrite=False))
            # and 'new' should not have been created
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children: self.failIf(u"new" in children))
            d.addCallback(lambda res: n.get_metadata_for(u"f1"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))
            d.addCallback(lambda res: n.get_metadata_for(u"f2"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))
            d.addCallback(lambda res: n.get_metadata_for(u"f3"))
            d.addCallback(lambda metadata:
                          self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                          (metadata["key"] == "value"), metadata))

            d.addCallback(lambda res: n.delete(u"f1"))
            d.addCallback(lambda res: n.delete(u"f2"))
            d.addCallback(lambda res: n.delete(u"f3"))


            d.addCallback(lambda res:
                          n.set_metadata_for(u"child",
                                             {"tags": ["web2.0-compatible"], "tahoe": {"bad": "mojo"}}))
            d.addCallback(lambda n1: n1.get_metadata_for(u"child"))
            d.addCallback(lambda metadata:
                          self.failUnless((set(metadata.keys()) == set(["tags", "tahoe"])) and
                                          metadata["tags"] == ["web2.0-compatible"] and
                                          "bad" not in metadata["tahoe"], metadata))

            d.addCallback(lambda res:
                          self.shouldFail(NoSuchChildError, "set_metadata_for-nosuch", "",
                                          n.set_metadata_for, u"nosuch", {}))


            def _start(res):
                self._start_timestamp = time.time()
            d.addCallback(_start)
            # a long time ago, we used simplejson-1.7.1 (as shipped on Ubuntu
            # 'gutsy'), which had a bug/misbehavior in which it would round
            # all floats to hundredeths (it used str(num) instead of
            # repr(num)). To prevent this bug from causing the test to fail,
            # we stall for more than a few hundrededths of a second here.
            # simplejson-1.7.3 does not have this bug, and anyways we've
            # moved on to stdlib "json" which doesn't have it either.
            d.addCallback(self.stall, 0.1)
            d.addCallback(lambda res: n.add_file(u"timestamps",
                                                 upload.Data("stamp me", convergence="some convergence string")))
            d.addCallback(self.stall, 0.1)
            def _stop(res):
                self._stop_timestamp = time.time()
            d.addCallback(_stop)

            d.addCallback(lambda res: n.get_metadata_for(u"timestamps"))
            def _check_timestamp1(metadata):
                self.failUnlessEqual(set(metadata.keys()), set(["tahoe"]))
                tahoe_md = metadata["tahoe"]
                self.failUnlessEqual(set(tahoe_md.keys()), set(["linkcrtime", "linkmotime"]))

                self.failUnlessGreaterOrEqualThan(tahoe_md["linkcrtime"],
                                                  self._start_timestamp)
                self.failUnlessGreaterOrEqualThan(self._stop_timestamp,
                                                  tahoe_md["linkcrtime"])
                self.failUnlessGreaterOrEqualThan(tahoe_md["linkmotime"],
                                                  self._start_timestamp)
                self.failUnlessGreaterOrEqualThan(self._stop_timestamp,
                                                  tahoe_md["linkmotime"])
                # Our current timestamp rules say that replacing an existing
                # child should preserve the 'linkcrtime' but update the
                # 'linkmotime'
                self._old_linkcrtime = tahoe_md["linkcrtime"]
                self._old_linkmotime = tahoe_md["linkmotime"]
            d.addCallback(_check_timestamp1)
            d.addCallback(self.stall, 2.0) # accomodate low-res timestamps
            d.addCallback(lambda res: n.set_node(u"timestamps", n))
            d.addCallback(lambda res: n.get_metadata_for(u"timestamps"))
            def _check_timestamp2(metadata):
                self.failUnlessIn("tahoe", metadata)
                tahoe_md = metadata["tahoe"]
                self.failUnlessEqual(set(tahoe_md.keys()), set(["linkcrtime", "linkmotime"]))

                self.failUnlessReallyEqual(tahoe_md["linkcrtime"], self._old_linkcrtime)
                self.failUnlessGreaterThan(tahoe_md["linkmotime"], self._old_linkmotime)
                return n.delete(u"timestamps")
            d.addCallback(_check_timestamp2)

            d.addCallback(lambda res: n.delete(u"subdir"))
            d.addCallback(lambda old_child:
                          self.failUnlessReallyEqual(old_child.get_uri(),
                                                     self.subdir.get_uri()))

            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(set(children.keys()),
                                                     set([u"child"])))

            uploadable1 = upload.Data("some data", convergence="converge")
            d.addCallback(lambda res: n.add_file(u"newfile", uploadable1))
            d.addCallback(lambda newnode:
                          self.failUnless(IImmutableFileNode.providedBy(newnode)))
            uploadable2 = upload.Data("some data", convergence="stuff")
            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "add_file-no",
                                          "child 'newfile' already exists",
                                          n.add_file, u"newfile",
                                          uploadable2,
                                          overwrite=False))
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(set(children.keys()),
                                                     set([u"child", u"newfile"])))
            d.addCallback(lambda res: n.get_metadata_for(u"newfile"))
            d.addCallback(lambda metadata:
                          self.failUnlessEqual(set(metadata.keys()), set(["tahoe"])))

            uploadable3 = upload.Data("some data", convergence="converge")
            d.addCallback(lambda res: n.add_file(u"newfile-metadata",
                                                 uploadable3,
                                                 {"key": "value"}))
            d.addCallback(lambda newnode:
                          self.failUnless(IImmutableFileNode.providedBy(newnode)))
            d.addCallback(lambda res: n.get_metadata_for(u"newfile-metadata"))
            d.addCallback(lambda metadata:
                              self.failUnless((set(metadata.keys()) == set(["key", "tahoe"])) and
                                              (metadata['key'] == "value"), metadata))
            d.addCallback(lambda res: n.delete(u"newfile-metadata"))

            d.addCallback(lambda res: n.create_subdirectory(u"subdir2"))
            def _created2(subdir2):
                self.subdir2 = subdir2
                # put something in the way, to make sure it gets overwritten
                return subdir2.add_file(u"child", upload.Data("overwrite me",
                                                              "converge"))
            d.addCallback(_created2)

            d.addCallback(lambda res:
                          n.move_child_to(u"child", self.subdir2))
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(set(children.keys()),
                                                     set([u"newfile", u"subdir2"])))
            d.addCallback(lambda res: self.subdir2.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(set(children.keys()),
                                                     set([u"child"])))
            d.addCallback(lambda res: self.subdir2.get(u"child"))
            d.addCallback(lambda child:
                          self.failUnlessReallyEqual(child.get_uri(),
                                                     fake_file_uri))

            # move it back, using new_child_name=
            d.addCallback(lambda res:
                          self.subdir2.move_child_to(u"child", n, u"newchild"))
            d.addCallback(lambda res: n.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(set(children.keys()),
                                                     set([u"newchild", u"newfile",
                                                          u"subdir2"])))
            d.addCallback(lambda res: self.subdir2.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(set(children.keys()), set([])))

            # now make sure that we honor overwrite=False
            d.addCallback(lambda res:
                          self.subdir2.set_uri(u"newchild",
                                               other_file_uri, other_file_uri))

            d.addCallback(lambda res:
                          self.shouldFail(ExistingChildError, "move_child_to-no",
                                          "child 'newchild' already exists",
                                          n.move_child_to, u"newchild",
                                          self.subdir2,
                                          overwrite=False))
            d.addCallback(lambda res: self.subdir2.get(u"newchild"))
            d.addCallback(lambda child:
                          self.failUnlessReallyEqual(child.get_uri(),
                                                     other_file_uri))


            # Setting the no-write field should diminish a mutable cap to read-only
            # (for both files and directories).

            d.addCallback(lambda ign: n.set_uri(u"mutable", other_file_uri, other_file_uri))
            d.addCallback(lambda ign: n.get(u"mutable"))
            d.addCallback(lambda mutable: self.failIf(mutable.is_readonly(), mutable))
            d.addCallback(lambda ign: n.set_metadata_for(u"mutable", {"no-write": True}))
            d.addCallback(lambda ign: n.get(u"mutable"))
            d.addCallback(lambda mutable: self.failUnless(mutable.is_readonly(), mutable))
            d.addCallback(lambda ign: n.set_metadata_for(u"mutable", {"no-write": True}))
            d.addCallback(lambda ign: n.get(u"mutable"))
            d.addCallback(lambda mutable: self.failUnless(mutable.is_readonly(), mutable))

            d.addCallback(lambda ign: n.get(u"subdir2"))
            d.addCallback(lambda subdir2: self.failIf(subdir2.is_readonly()))
            d.addCallback(lambda ign: n.set_metadata_for(u"subdir2", {"no-write": True}))
            d.addCallback(lambda ign: n.get(u"subdir2"))
            d.addCallback(lambda subdir2: self.failUnless(subdir2.is_readonly(), subdir2))

            d.addCallback(lambda ign: n.set_uri(u"mutable_ro", other_file_uri, other_file_uri,
                                                metadata={"no-write": True}))
            d.addCallback(lambda ign: n.get(u"mutable_ro"))
            d.addCallback(lambda mutable_ro: self.failUnless(mutable_ro.is_readonly(), mutable_ro))

            d.addCallback(lambda ign: n.create_subdirectory(u"subdir_ro", metadata={"no-write": True}))
            d.addCallback(lambda ign: n.get(u"subdir_ro"))
            d.addCallback(lambda subdir_ro: self.failUnless(subdir_ro.is_readonly(), subdir_ro))

            return d

        d.addCallback(_then)

        d.addErrback(self.explain_error)
        return d


    def _do_initial_children_test(self, mdmf=False):
        c = self.g.clients[0]
        nm = c.nodemaker

        kids = {one_nfd: (nm.create_from_cap(one_uri), {}),
                u"two": (nm.create_from_cap(setup_py_uri),
                         {"metakey": "metavalue"}),
                u"mut": (nm.create_from_cap(mut_write_uri, mut_read_uri), {}),
                u"mdmf": (nm.create_from_cap(mdmf_write_uri, mdmf_read_uri), {}),
                u"fut": (nm.create_from_cap(future_write_uri, future_read_uri), {}),
                u"fro": (nm.create_from_cap(None, future_read_uri), {}),
                u"fut-unic": (nm.create_from_cap(future_nonascii_write_uri, future_nonascii_read_uri), {}),
                u"fro-unic": (nm.create_from_cap(None, future_nonascii_read_uri), {}),
                u"empty_litdir": (nm.create_from_cap(empty_litdir_uri), {}),
                u"tiny_litdir": (nm.create_from_cap(tiny_litdir_uri), {}),
                }
        if mdmf:
            d = c.create_dirnode(kids, version=MDMF_VERSION)
        else:
            d = c.create_dirnode(kids)

        def _created(dn):
            self.failUnless(isinstance(dn, dirnode.DirectoryNode))
            backing_node = dn._node
            if mdmf:
                self.failUnlessEqual(backing_node.get_version(),
                                     MDMF_VERSION)
            else:
                self.failUnlessEqual(backing_node.get_version(),
                                     SDMF_VERSION)
            self.failUnless(dn.is_mutable())
            self.failIf(dn.is_readonly())
            self.failIf(dn.is_unknown())
            self.failIf(dn.is_allowed_in_immutable_directory())
            dn.raise_error()
            rep = str(dn)
            self.failUnless("RW-MUT" in rep)
            return dn.list()
        d.addCallback(_created)

        def _check_kids(children):
            self.failUnlessReallyEqual(set(children.keys()),
                                       set([one_nfc, u"two", u"mut", u"mdmf", u"fut", u"fro",
                                        u"fut-unic", u"fro-unic", u"empty_litdir", u"tiny_litdir"]))
            one_node, one_metadata = children[one_nfc]
            two_node, two_metadata = children[u"two"]
            mut_node, mut_metadata = children[u"mut"]
            mdmf_node, mdmf_metadata = children[u"mdmf"]
            fut_node, fut_metadata = children[u"fut"]
            fro_node, fro_metadata = children[u"fro"]
            futna_node, futna_metadata = children[u"fut-unic"]
            frona_node, frona_metadata = children[u"fro-unic"]
            emptylit_node, emptylit_metadata = children[u"empty_litdir"]
            tinylit_node, tinylit_metadata = children[u"tiny_litdir"]

            self.failUnlessReallyEqual(one_node.get_size(), 3)
            self.failUnlessReallyEqual(one_node.get_uri(), one_uri)
            self.failUnlessReallyEqual(one_node.get_readonly_uri(), one_uri)
            self.failUnless(isinstance(one_metadata, dict), one_metadata)

            self.failUnlessReallyEqual(two_node.get_size(), 14861)
            self.failUnlessReallyEqual(two_node.get_uri(), setup_py_uri)
            self.failUnlessReallyEqual(two_node.get_readonly_uri(), setup_py_uri)
            self.failUnlessEqual(two_metadata["metakey"], "metavalue")

            self.failUnlessReallyEqual(mut_node.get_uri(), mut_write_uri)
            self.failUnlessReallyEqual(mut_node.get_readonly_uri(), mut_read_uri)
            self.failUnless(isinstance(mut_metadata, dict), mut_metadata)

            self.failUnlessReallyEqual(mdmf_node.get_uri(), mdmf_write_uri)
            self.failUnlessReallyEqual(mdmf_node.get_readonly_uri(), mdmf_read_uri)
            self.failUnless(isinstance(mdmf_metadata, dict), mdmf_metadata)

            self.failUnless(fut_node.is_unknown())
            self.failUnlessReallyEqual(fut_node.get_uri(), future_write_uri)
            self.failUnlessReallyEqual(fut_node.get_readonly_uri(), "ro." + future_read_uri)
            self.failUnless(isinstance(fut_metadata, dict), fut_metadata)

            self.failUnless(futna_node.is_unknown())
            self.failUnlessReallyEqual(futna_node.get_uri(), future_nonascii_write_uri)
            self.failUnlessReallyEqual(futna_node.get_readonly_uri(), "ro." + future_nonascii_read_uri)
            self.failUnless(isinstance(futna_metadata, dict), futna_metadata)

            self.failUnless(fro_node.is_unknown())
            self.failUnlessReallyEqual(fro_node.get_uri(), "ro." + future_read_uri)
            self.failUnlessReallyEqual(fut_node.get_readonly_uri(), "ro." + future_read_uri)
            self.failUnless(isinstance(fro_metadata, dict), fro_metadata)

            self.failUnless(frona_node.is_unknown())
            self.failUnlessReallyEqual(frona_node.get_uri(), "ro." + future_nonascii_read_uri)
            self.failUnlessReallyEqual(futna_node.get_readonly_uri(), "ro." + future_nonascii_read_uri)
            self.failUnless(isinstance(frona_metadata, dict), frona_metadata)

            self.failIf(emptylit_node.is_unknown())
            self.failUnlessReallyEqual(emptylit_node.get_storage_index(), None)
            self.failIf(tinylit_node.is_unknown())
            self.failUnlessReallyEqual(tinylit_node.get_storage_index(), None)

            d2 = defer.succeed(None)
            d2.addCallback(lambda ignored: emptylit_node.list())
            d2.addCallback(lambda children: self.failUnlessEqual(children, {}))
            d2.addCallback(lambda ignored: tinylit_node.list())
            d2.addCallback(lambda children: self.failUnlessReallyEqual(set(children.keys()),
                                                                       set([u"short"])))
            d2.addCallback(lambda ignored: tinylit_node.list())
            d2.addCallback(lambda children: children[u"short"][0].read(MemAccum()))
            d2.addCallback(lambda accum: self.failUnlessReallyEqual(accum.data, "The end."))
            return d2
        d.addCallback(_check_kids)

        d.addCallback(lambda ign: nm.create_new_mutable_directory(kids))
        d.addCallback(lambda dn: dn.list())
        d.addCallback(_check_kids)

        bad_future_node = UnknownNode(future_write_uri, None)
        bad_kids1 = {one_nfd: (bad_future_node, {})}
        # This should fail because we don't know how to diminish the future_write_uri
        # cap (given in a write slot and not prefixed with "ro." or "imm.") to a readcap.
        d.addCallback(lambda ign:
                      self.shouldFail(MustNotBeUnknownRWError, "bad_kids1",
                                      "cannot attach unknown",
                                      nm.create_new_mutable_directory,
                                      bad_kids1))
        bad_kids2 = {one_nfd: (nm.create_from_cap(one_uri), None)}
        d.addCallback(lambda ign:
                      self.shouldFail(AssertionError, "bad_kids2",
                                      "requires metadata to be a dict",
                                      nm.create_new_mutable_directory,
                                      bad_kids2))
        return d

    def _do_basic_test(self, mdmf=False):
        c = self.g.clients[0]
        d = None
        if mdmf:
            d = c.create_dirnode(version=MDMF_VERSION)
        else:
            d = c.create_dirnode()
        def _done(res):
            self.failUnless(isinstance(res, dirnode.DirectoryNode))
            self.failUnless(res.is_mutable())
            self.failIf(res.is_readonly())
            self.failIf(res.is_unknown())
            self.failIf(res.is_allowed_in_immutable_directory())
            res.raise_error()
            rep = str(res)
            self.failUnless("RW-MUT" in rep)
        d.addCallback(_done)
        return d

    def test_basic(self):
        self.basedir = "dirnode/Dirnode/test_basic"
        self.set_up_grid(oneshare=True)
        return self._do_basic_test()

    def test_basic_mdmf(self):
        self.basedir = "dirnode/Dirnode/test_basic_mdmf"
        self.set_up_grid(oneshare=True)
        return self._do_basic_test(mdmf=True)

    def test_initial_children(self):
        self.basedir = "dirnode/Dirnode/test_initial_children"
        self.set_up_grid(oneshare=True)
        return self._do_initial_children_test()

    def test_immutable(self):
        self.basedir = "dirnode/Dirnode/test_immutable"
        self.set_up_grid(oneshare=True)
        c = self.g.clients[0]
        nm = c.nodemaker

        kids = {one_nfd: (nm.create_from_cap(one_uri), {}),
                u"two": (nm.create_from_cap(setup_py_uri),
                         {"metakey": "metavalue"}),
                u"fut": (nm.create_from_cap(None, future_read_uri), {}),
                u"futna": (nm.create_from_cap(None, future_nonascii_read_uri), {}),
                u"empty_litdir": (nm.create_from_cap(empty_litdir_uri), {}),
                u"tiny_litdir": (nm.create_from_cap(tiny_litdir_uri), {}),
                }
        d = c.create_immutable_dirnode(kids)

        def _created(dn):
            self.failUnless(isinstance(dn, dirnode.DirectoryNode))
            self.failIf(dn.is_mutable())
            self.failUnless(dn.is_readonly())
            self.failIf(dn.is_unknown())
            self.failUnless(dn.is_allowed_in_immutable_directory())
            dn.raise_error()
            rep = str(dn)
            self.failUnless("RO-IMM" in rep)
            cap = dn.get_cap()
            self.failUnlessIn("CHK", cap.to_string())
            self.cap = cap
            return dn.list()
        d.addCallback(_created)

        def _check_kids(children):
            self.failUnlessReallyEqual(set(children.keys()),
                                       set([one_nfc, u"two", u"fut", u"futna", u"empty_litdir", u"tiny_litdir"]))
            one_node, one_metadata = children[one_nfc]
            two_node, two_metadata = children[u"two"]
            fut_node, fut_metadata = children[u"fut"]
            futna_node, futna_metadata = children[u"futna"]
            emptylit_node, emptylit_metadata = children[u"empty_litdir"]
            tinylit_node, tinylit_metadata = children[u"tiny_litdir"]

            self.failUnlessReallyEqual(one_node.get_size(), 3)
            self.failUnlessReallyEqual(one_node.get_uri(), one_uri)
            self.failUnlessReallyEqual(one_node.get_readonly_uri(), one_uri)
            self.failUnless(isinstance(one_metadata, dict), one_metadata)

            self.failUnlessReallyEqual(two_node.get_size(), 14861)
            self.failUnlessReallyEqual(two_node.get_uri(), setup_py_uri)
            self.failUnlessReallyEqual(two_node.get_readonly_uri(), setup_py_uri)
            self.failUnlessEqual(two_metadata["metakey"], "metavalue")

            self.failUnless(fut_node.is_unknown())
            self.failUnlessReallyEqual(fut_node.get_uri(), "imm." + future_read_uri)
            self.failUnlessReallyEqual(fut_node.get_readonly_uri(), "imm." + future_read_uri)
            self.failUnless(isinstance(fut_metadata, dict), fut_metadata)

            self.failUnless(futna_node.is_unknown())
            self.failUnlessReallyEqual(futna_node.get_uri(), "imm." + future_nonascii_read_uri)
            self.failUnlessReallyEqual(futna_node.get_readonly_uri(), "imm." + future_nonascii_read_uri)
            self.failUnless(isinstance(futna_metadata, dict), futna_metadata)

            self.failIf(emptylit_node.is_unknown())
            self.failUnlessReallyEqual(emptylit_node.get_storage_index(), None)
            self.failIf(tinylit_node.is_unknown())
            self.failUnlessReallyEqual(tinylit_node.get_storage_index(), None)

            d2 = defer.succeed(None)
            d2.addCallback(lambda ignored: emptylit_node.list())
            d2.addCallback(lambda children: self.failUnlessEqual(children, {}))
            d2.addCallback(lambda ignored: tinylit_node.list())
            d2.addCallback(lambda children: self.failUnlessReallyEqual(set(children.keys()),
                                                                       set([u"short"])))
            d2.addCallback(lambda ignored: tinylit_node.list())
            d2.addCallback(lambda children: children[u"short"][0].read(MemAccum()))
            d2.addCallback(lambda accum: self.failUnlessReallyEqual(accum.data, "The end."))
            return d2

        d.addCallback(_check_kids)

        d.addCallback(lambda ign: nm.create_from_cap(self.cap.to_string()))
        d.addCallback(lambda dn: dn.list())
        d.addCallback(_check_kids)

        bad_future_node1 = UnknownNode(future_write_uri, None)
        bad_kids1 = {one_nfd: (bad_future_node1, {})}
        d.addCallback(lambda ign:
                      self.shouldFail(MustNotBeUnknownRWError, "bad_kids1",
                                      "cannot attach unknown",
                                      c.create_immutable_dirnode,
                                      bad_kids1))
        bad_future_node2 = UnknownNode(future_write_uri, future_read_uri)
        bad_kids2 = {one_nfd: (bad_future_node2, {})}
        d.addCallback(lambda ign:
                      self.shouldFail(MustBeDeepImmutableError, "bad_kids2",
                                      "is not allowed in an immutable directory",
                                      c.create_immutable_dirnode,
                                      bad_kids2))
        bad_kids3 = {one_nfd: (nm.create_from_cap(one_uri), None)}
        d.addCallback(lambda ign:
                      self.shouldFail(AssertionError, "bad_kids3",
                                      "requires metadata to be a dict",
                                      c.create_immutable_dirnode,
                                      bad_kids3))
        bad_kids4 = {one_nfd: (nm.create_from_cap(mut_write_uri), {})}
        d.addCallback(lambda ign:
                      self.shouldFail(MustBeDeepImmutableError, "bad_kids4",
                                      "is not allowed in an immutable directory",
                                      c.create_immutable_dirnode,
                                      bad_kids4))
        bad_kids5 = {one_nfd: (nm.create_from_cap(mut_read_uri), {})}
        d.addCallback(lambda ign:
                      self.shouldFail(MustBeDeepImmutableError, "bad_kids5",
                                      "is not allowed in an immutable directory",
                                      c.create_immutable_dirnode,
                                      bad_kids5))
        bad_kids6 = {one_nfd: (nm.create_from_cap(mdmf_write_uri), {})}
        d.addCallback(lambda ign:
                      self.shouldFail(MustBeDeepImmutableError, "bad_kids6",
                                      "is not allowed in an immutable directory",
                                      c.create_immutable_dirnode,
                                      bad_kids6))
        bad_kids7 = {one_nfd: (nm.create_from_cap(mdmf_read_uri), {})}
        d.addCallback(lambda ign:
                      self.shouldFail(MustBeDeepImmutableError, "bad_kids7",
                                      "is not allowed in an immutable directory",
                                      c.create_immutable_dirnode,
                                      bad_kids7))
        d.addCallback(lambda ign: c.create_immutable_dirnode({}))
        def _created_empty(dn):
            self.failUnless(isinstance(dn, dirnode.DirectoryNode))
            self.failIf(dn.is_mutable())
            self.failUnless(dn.is_readonly())
            self.failIf(dn.is_unknown())
            self.failUnless(dn.is_allowed_in_immutable_directory())
            dn.raise_error()
            rep = str(dn)
            self.failUnless("RO-IMM" in rep)
            cap = dn.get_cap()
            self.failUnlessIn("LIT", cap.to_string())
            self.failUnlessReallyEqual(cap.to_string(), "URI:DIR2-LIT:")
            self.cap = cap
            return dn.list()
        d.addCallback(_created_empty)
        d.addCallback(lambda kids: self.failUnlessEqual(kids, {}))
        smallkids = {u"o": (nm.create_from_cap(one_uri), {})}
        d.addCallback(lambda ign: c.create_immutable_dirnode(smallkids))
        def _created_small(dn):
            self.failUnless(isinstance(dn, dirnode.DirectoryNode))
            self.failIf(dn.is_mutable())
            self.failUnless(dn.is_readonly())
            self.failIf(dn.is_unknown())
            self.failUnless(dn.is_allowed_in_immutable_directory())
            dn.raise_error()
            rep = str(dn)
            self.failUnless("RO-IMM" in rep)
            cap = dn.get_cap()
            self.failUnlessIn("LIT", cap.to_string())
            self.failUnlessReallyEqual(cap.to_string(),
                                       "URI:DIR2-LIT:gi4tumj2n4wdcmz2kvjesosmjfkdu3rvpbtwwlbqhiwdeot3puwcy")
            self.cap = cap
            return dn.list()
        d.addCallback(_created_small)
        d.addCallback(lambda kids: self.failUnlessReallyEqual(kids.keys(), [u"o"]))

        # now test n.create_subdirectory(mutable=False)
        d.addCallback(lambda ign: c.create_dirnode())
        def _made_parent(n):
            d = n.create_subdirectory(u"subdir", kids, mutable=False)
            d.addCallback(lambda sd: sd.list())
            d.addCallback(_check_kids)
            d.addCallback(lambda ign: n.list())
            d.addCallback(lambda children:
                          self.failUnlessReallyEqual(children.keys(), [u"subdir"]))
            d.addCallback(lambda ign: n.get(u"subdir"))
            d.addCallback(lambda sd: sd.list())
            d.addCallback(_check_kids)
            d.addCallback(lambda ign: n.get(u"subdir"))
            d.addCallback(lambda sd: self.failIf(sd.is_mutable()))
            bad_kids8 = {one_nfd: (nm.create_from_cap(mut_write_uri), {})}
            d.addCallback(lambda ign:
                          self.shouldFail(MustBeDeepImmutableError, "bad_kids8",
                                          "is not allowed in an immutable directory",
                                          n.create_subdirectory,
                                          u"sub2", bad_kids8, mutable=False))
            bad_kids9 = {one_nfd: (nm.create_from_cap(mdmf_write_uri), {})}
            d.addCallback(lambda ign:
                          self.shouldFail(MustBeDeepImmutableError, "bad_kids9",
                                          "is not allowed in an immutable directory",
                                          n.create_subdirectory,
                                          u"sub2", bad_kids9, mutable=False))
            return d
        d.addCallback(_made_parent)
        return d

    def test_directory_representation(self):
        self.basedir = "dirnode/Dirnode/test_directory_representation"
        self.set_up_grid(oneshare=True)
        c = self.g.clients[0]
        nm = c.nodemaker

        # This test checks that any trailing spaces in URIs are retained in the
        # encoded directory, but stripped when we get them out of the directory.
        # See ticket #925 for why we want that.
        # It also tests that we store child names as UTF-8 NFC, and normalize
        # them again when retrieving them.

        stripped_write_uri = "lafs://from_the_future\t"
        stripped_read_uri = "lafs://readonly_from_the_future\t"
        spacedout_write_uri = stripped_write_uri + "  "
        spacedout_read_uri = stripped_read_uri + "  "

        child = nm.create_from_cap(spacedout_write_uri, spacedout_read_uri)
        self.failUnlessReallyEqual(child.get_write_uri(), spacedout_write_uri)
        self.failUnlessReallyEqual(child.get_readonly_uri(), "ro." + spacedout_read_uri)

        child_dottedi = u"ch\u0131\u0307ld"

        kids_in   = {child_dottedi: (child, {}), one_nfd: (child, {})}
        kids_out  = {child_dottedi: (child, {}), one_nfc: (child, {})}
        kids_norm = {u"child":      (child, {}), one_nfc: (child, {})}
        d = c.create_dirnode(kids_in)

        def _created(dn):
            self.failUnless(isinstance(dn, dirnode.DirectoryNode))
            self.failUnless(dn.is_mutable())
            self.failIf(dn.is_readonly())
            dn.raise_error()
            self.cap = dn.get_cap()
            self.rootnode = dn
            return dn._node.download_best_version()
        d.addCallback(_created)

        def _check_data(data):
            # Decode the netstring representation of the directory to check that the
            # spaces are retained when the URIs are stored, and that the names are stored
            # as NFC.
            position = 0
            numkids = 0
            while position < len(data):
                entries, position = split_netstring(data, 1, position)
                entry = entries[0]
                (name_utf8, ro_uri, rwcapdata, metadata_s), subpos = split_netstring(entry, 4)
                name = name_utf8.decode("utf-8")
                rw_uri = self.rootnode._decrypt_rwcapdata(rwcapdata)
                self.failUnlessIn(name, kids_out)
                (expected_child, ign) = kids_out[name]
                self.failUnlessReallyEqual(rw_uri, expected_child.get_write_uri())
                self.failUnlessReallyEqual("ro." + ro_uri, expected_child.get_readonly_uri())
                numkids += 1

            self.failUnlessReallyEqual(numkids, len(kids_out))
            return self.rootnode
        d.addCallback(_check_data)

        # Mock up a hypothetical future version of Unicode that adds a canonical equivalence
        # between dotless-i + dot-above, and 'i'. That would actually be prohibited by the
        # stability rules, but similar additions involving currently-unassigned characters
        # would not be.
        old_normalize = unicodedata.normalize
        def future_normalize(form, s):
            assert form == 'NFC', form
            return old_normalize(form, s).replace(u"\u0131\u0307", u"i")

        def _list(node):
            unicodedata.normalize = future_normalize
            d2 = node.list()
            def _undo_mock(res):
                unicodedata.normalize = old_normalize
                return res
            d2.addBoth(_undo_mock)
            return d2
        d.addCallback(_list)

        def _check_kids(children):
            # Now when we use the real directory listing code, the trailing spaces
            # should have been stripped (and "ro." should have been prepended to the
            # ro_uri, since it's unknown). Also the dotless-i + dot-above should have been
            # normalized to 'i'.

            self.failUnlessReallyEqual(set(children.keys()), set(kids_norm.keys()))
            child_node, child_metadata = children[u"child"]

            self.failUnlessReallyEqual(child_node.get_write_uri(), stripped_write_uri)
            self.failUnlessReallyEqual(child_node.get_readonly_uri(), "ro." + stripped_read_uri)
        d.addCallback(_check_kids)

        d.addCallback(lambda ign: nm.create_from_cap(self.cap.to_string()))
        d.addCallback(_list)
        d.addCallback(_check_kids)  # again with dirnode recreated from cap
        return d

    def test_check(self):
        self.basedir = "dirnode/Dirnode/test_check"
        self.set_up_grid(oneshare=True)
        c = self.g.clients[0]
        d = c.create_dirnode()
        d.addCallback(lambda dn: dn.check(Monitor()))
        def _done(res):
            self.failUnless(res.is_healthy())
        d.addCallback(_done)
        return d

    def _test_deepcheck_create(self, version=SDMF_VERSION):
        # create a small tree with a loop, and some non-directories
        #  root/
        #  root/subdir/
        #  root/subdir/file1
        #  root/subdir/link -> root
        #  root/rodir
        c = self.g.clients[0]
        d = c.create_dirnode(version=version)
        def _created_root(rootnode):
            self._rootnode = rootnode
            self.failUnlessEqual(rootnode._node.get_version(), version)
            return rootnode.create_subdirectory(u"subdir")
        d.addCallback(_created_root)
        def _created_subdir(subdir):
            self._subdir = subdir
            d = subdir.add_file(u"file1", upload.Data("data"*100, None))
            d.addCallback(lambda res: subdir.set_node(u"link", self._rootnode))
            d.addCallback(lambda res: c.create_dirnode())
            d.addCallback(lambda dn:
                          self._rootnode.set_uri(u"rodir",
                                                 dn.get_uri(),
                                                 dn.get_readonly_uri()))
            return d
        d.addCallback(_created_subdir)
        def _done(res):
            return self._rootnode
        d.addCallback(_done)
        return d

    def test_deepcheck(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck"
        self.set_up_grid(oneshare=True)
        d = self._test_deepcheck_create()
        d.addCallback(lambda rootnode: rootnode.start_deep_check().when_done())
        def _check_results(r):
            self.failUnless(IDeepCheckResults.providedBy(r))
            c = r.get_counters()
            self.failUnlessReallyEqual(c,
                                       {"count-objects-checked": 4,
                                        "count-objects-healthy": 4,
                                        "count-objects-unhealthy": 0,
                                        "count-objects-unrecoverable": 0,
                                        "count-corrupt-shares": 0,
                                        })
            self.failIf(r.get_corrupt_shares())
            self.failUnlessReallyEqual(len(r.get_all_results()), 4)
        d.addCallback(_check_results)
        return d

    def test_deepcheck_cachemisses(self):
        self.basedir = "dirnode/Dirnode/test_mdmf_cachemisses"
        self.set_up_grid(oneshare=True)
        d = self._test_deepcheck_create()
        # Clear the counters and set the rootnode
        d.addCallback(lambda rootnode:
                      not [ss._clear_counters() for ss
                           in self.g.wrappers_by_id.values()] or rootnode)
        d.addCallback(lambda rootnode: rootnode.start_deep_check().when_done())
        def _check(ign):
            count = sum([ss.counter_by_methname['slot_readv']
                         for ss in self.g.wrappers_by_id.values()])
            self.failIf(count > 60, 'Expected only 60 cache misses,'
                                    'unfortunately there were %d' % (count,))
        d.addCallback(_check)
        return d

    def test_deepcheck_mdmf(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck_mdmf"
        self.set_up_grid(oneshare=True)
        d = self._test_deepcheck_create(MDMF_VERSION)
        d.addCallback(lambda rootnode: rootnode.start_deep_check().when_done())
        def _check_results(r):
            self.failUnless(IDeepCheckResults.providedBy(r))
            c = r.get_counters()
            self.failUnlessReallyEqual(c,
                                       {"count-objects-checked": 4,
                                        "count-objects-healthy": 4,
                                        "count-objects-unhealthy": 0,
                                        "count-objects-unrecoverable": 0,
                                        "count-corrupt-shares": 0,
                                        })
            self.failIf(r.get_corrupt_shares())
            self.failUnlessReallyEqual(len(r.get_all_results()), 4)
        d.addCallback(_check_results)
        return d

    def test_deepcheck_and_repair(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck_and_repair"
        self.set_up_grid(oneshare=True)
        d = self._test_deepcheck_create()
        d.addCallback(lambda rootnode:
                      rootnode.start_deep_check_and_repair().when_done())
        def _check_results(r):
            self.failUnless(IDeepCheckAndRepairResults.providedBy(r))
            c = r.get_counters()
            self.failUnlessReallyEqual(c,
                                       {"count-objects-checked": 4,
                                        "count-objects-healthy-pre-repair": 4,
                                        "count-objects-unhealthy-pre-repair": 0,
                                        "count-objects-unrecoverable-pre-repair": 0,
                                        "count-corrupt-shares-pre-repair": 0,
                                        "count-objects-healthy-post-repair": 4,
                                        "count-objects-unhealthy-post-repair": 0,
                                        "count-objects-unrecoverable-post-repair": 0,
                                        "count-corrupt-shares-post-repair": 0,
                                        "count-repairs-attempted": 0,
                                        "count-repairs-successful": 0,
                                        "count-repairs-unsuccessful": 0,
                                        })
            self.failIf(r.get_corrupt_shares())
            self.failIf(r.get_remaining_corrupt_shares())
            self.failUnlessReallyEqual(len(r.get_all_results()), 4)
        d.addCallback(_check_results)
        return d

    def test_deepcheck_and_repair_mdmf(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck_and_repair_mdmf"
        self.set_up_grid(oneshare=True)
        d = self._test_deepcheck_create(version=MDMF_VERSION)
        d.addCallback(lambda rootnode:
                      rootnode.start_deep_check_and_repair().when_done())
        def _check_results(r):
            self.failUnless(IDeepCheckAndRepairResults.providedBy(r))
            c = r.get_counters()
            self.failUnlessReallyEqual(c,
                                       {"count-objects-checked": 4,
                                        "count-objects-healthy-pre-repair": 4,
                                        "count-objects-unhealthy-pre-repair": 0,
                                        "count-objects-unrecoverable-pre-repair": 0,
                                        "count-corrupt-shares-pre-repair": 0,
                                        "count-objects-healthy-post-repair": 4,
                                        "count-objects-unhealthy-post-repair": 0,
                                        "count-objects-unrecoverable-post-repair": 0,
                                        "count-corrupt-shares-post-repair": 0,
                                        "count-repairs-attempted": 0,
                                        "count-repairs-successful": 0,
                                        "count-repairs-unsuccessful": 0,
                                        })
            self.failIf(r.get_corrupt_shares())
            self.failIf(r.get_remaining_corrupt_shares())
            self.failUnlessReallyEqual(len(r.get_all_results()), 4)
        d.addCallback(_check_results)
        return d

    def _mark_file_bad(self, rootnode):
        self.delete_shares_numbered(rootnode.get_uri(), [0])
        return rootnode

    def test_deepcheck_problems(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck_problems"
        self.set_up_grid()
        d = self._test_deepcheck_create()
        d.addCallback(lambda rootnode: self._mark_file_bad(rootnode))
        d.addCallback(lambda rootnode: rootnode.start_deep_check().when_done())
        def _check_results(r):
            c = r.get_counters()
            self.failUnlessReallyEqual(c,
                                       {"count-objects-checked": 4,
                                        "count-objects-healthy": 3,
                                        "count-objects-unhealthy": 1,
                                        "count-objects-unrecoverable": 0,
                                        "count-corrupt-shares": 0,
                                        })
            #self.failUnlessReallyEqual(len(r.get_problems()), 1) # TODO
        d.addCallback(_check_results)
        return d

    def test_deepcheck_problems_mdmf(self):
        self.basedir = "dirnode/Dirnode/test_deepcheck_problems_mdmf"
        self.set_up_grid()
        d = self._test_deepcheck_create(version=MDMF_VERSION)
        d.addCallback(lambda rootnode: self._mark_file_bad(rootnode))
        d.addCallback(lambda rootnode: rootnode.start_deep_check().when_done())
        def _check_results(r):
            c = r.get_counters()
            self.failUnlessReallyEqual(c,
                                       {"count-objects-checked": 4,
                                        "count-objects-healthy": 3,
                                        "count-objects-unhealthy": 1,
                                        "count-objects-unrecoverable": 0,
                                        "count-corrupt-shares": 0,
                                        })
            #self.failUnlessReallyEqual(len(r.get_problems()), 1) # TODO
        d.addCallback(_check_results)
        return d

    def _do_readonly_test(self, version=SDMF_VERSION):
        c = self.g.clients[0]
        nm = c.nodemaker
        filecap = make_chk_file_uri(1234)
        filenode = nm.create_from_cap(filecap)
        uploadable = upload.Data("some data", convergence="some convergence string")

        d = c.create_dirnode(version=version)
        def _created(rw_dn):
            backing_node = rw_dn._node
            self.failUnlessEqual(backing_node.get_version(), version)
            d2 = rw_dn.set_uri(u"child", filecap, filecap)
            d2.addCallback(lambda res: rw_dn)
            return d2
        d.addCallback(_created)

        def _ready(rw_dn):
            ro_uri = rw_dn.get_readonly_uri()
            ro_dn = c.create_node_from_uri(ro_uri)
            self.failUnless(ro_dn.is_readonly())
            self.failUnless(ro_dn.is_mutable())
            self.failIf(ro_dn.is_unknown())
            self.failIf(ro_dn.is_allowed_in_immutable_directory())
            ro_dn.raise_error()

            self.shouldFail(dirnode.NotWriteableError, "set_uri ro", None,
                            ro_dn.set_uri, u"newchild", filecap, filecap)
            self.shouldFail(dirnode.NotWriteableError, "set_uri ro", None,
                            ro_dn.set_node, u"newchild", filenode)
            self.shouldFail(dirnode.NotWriteableError, "set_nodes ro", None,
                            ro_dn.set_nodes, { u"newchild": (filenode, None) })
            self.shouldFail(dirnode.NotWriteableError, "set_uri ro", None,
                            ro_dn.add_file, u"newchild", uploadable)
            self.shouldFail(dirnode.NotWriteableError, "set_uri ro", None,
                            ro_dn.delete, u"child")
            self.shouldFail(dirnode.NotWriteableError, "set_uri ro", None,
                            ro_dn.create_subdirectory, u"newchild")
            self.shouldFail(dirnode.NotWriteableError, "set_metadata_for ro", None,
                            ro_dn.set_metadata_for, u"child", {})
            self.shouldFail(dirnode.NotWriteableError, "set_uri ro", None,
                            ro_dn.move_child_to, u"child", rw_dn)
            self.shouldFail(dirnode.NotWriteableError, "set_uri ro", None,
                            rw_dn.move_child_to, u"child", ro_dn)
            return ro_dn.list()
        d.addCallback(_ready)
        def _listed(children):
            self.failUnless(u"child" in children)
        d.addCallback(_listed)
        return d

    def test_readonly(self):
        self.basedir = "dirnode/Dirnode/test_readonly"
        self.set_up_grid(oneshare=True)
        return self._do_readonly_test()

    def test_readonly_mdmf(self):
        self.basedir = "dirnode/Dirnode/test_readonly_mdmf"
        self.set_up_grid(oneshare=True)
        return self._do_readonly_test(version=MDMF_VERSION)

    def failUnlessGreaterThan(self, a, b):
        self.failUnless(a > b, "%r should be > %r" % (a, b))

    def failUnlessGreaterOrEqualThan(self, a, b):
        self.failUnless(a >= b, "%r should be >= %r" % (a, b))

    def test_create(self):
        self.basedir = "dirnode/Dirnode/test_create"
        self.set_up_grid(oneshare=True)
        return self._do_create_test()

    def test_update_metadata(self):
        (t1, t2, t3) = (626644800.0, 634745640.0, 892226160.0)

        md1 = dirnode.update_metadata({"ctime": t1}, {}, t2)
        self.failUnlessEqual(md1, {"tahoe":{"linkcrtime": t1, "linkmotime": t2}})

        md2 = dirnode.update_metadata(md1, {"key": "value", "tahoe": {"bad": "mojo"}}, t3)
        self.failUnlessEqual(md2, {"key": "value",
                                   "tahoe":{"linkcrtime": t1, "linkmotime": t3}})

        md3 = dirnode.update_metadata({}, None, t3)
        self.failUnlessEqual(md3, {"tahoe":{"linkcrtime": t3, "linkmotime": t3}})

        md4 = dirnode.update_metadata({}, {"bool": True, "number": 42}, t1)
        self.failUnlessEqual(md4, {"bool": True, "number": 42,
                                   "tahoe":{"linkcrtime": t1, "linkmotime": t1}})

    def _do_create_subdirectory_test(self, version=SDMF_VERSION):
        c = self.g.clients[0]
        nm = c.nodemaker

        d = c.create_dirnode(version=version)
        def _then(n):
            # /
            self.rootnode = n
            fake_file_uri = make_mutable_file_uri()
            other_file_uri = make_mutable_file_uri()
            md = {"metakey": "metavalue"}
            kids = {u"kid1": (nm.create_from_cap(fake_file_uri), {}),
                    u"kid2": (nm.create_from_cap(other_file_uri), md),
                    }
            d = n.create_subdirectory(u"subdir", kids,
                                      mutable_version=version)
            def _check(sub):
                d = n.get_child_at_path(u"subdir")
                d.addCallback(lambda sub2: self.failUnlessReallyEqual(sub2.get_uri(),
                                                                      sub.get_uri()))
                d.addCallback(lambda ign: sub.list())
                return d
            d.addCallback(_check)
            def _check_kids(kids2):
                self.failUnlessEqual(set(kids.keys()), set(kids2.keys()))
                self.failUnlessEqual(kids2[u"kid2"][1]["metakey"], "metavalue")
            d.addCallback(_check_kids)
            return d
        d.addCallback(_then)
        return d

    def test_create_subdirectory(self):
        self.basedir = "dirnode/Dirnode/test_create_subdirectory"
        self.set_up_grid(oneshare=True)
        return self._do_create_subdirectory_test()

    def test_create_subdirectory_mdmf(self):
        self.basedir = "dirnode/Dirnode/test_create_subdirectory_mdmf"
        self.set_up_grid(oneshare=True)
        return self._do_create_subdirectory_test(version=MDMF_VERSION)

    def test_create_mdmf(self):
        self.basedir = "dirnode/Dirnode/test_mdmf"
        self.set_up_grid(oneshare=True)
        return self._do_create_test(mdmf=True)

    def test_mdmf_initial_children(self):
        self.basedir = "dirnode/Dirnode/test_mdmf"
        self.set_up_grid(oneshare=True)
        return self._do_initial_children_test(mdmf=True)

class MinimalFakeMutableFile(object):
    def get_writekey(self):
        return "writekey"

class Packing(testutil.ReallyEqualMixin, unittest.TestCase):
    # This is a base32-encoded representation of the directory tree
    # root/file1
    # root/file2
    # root/file3
    # as represented after being fed to _pack_contents.
    # We have it here so we can decode it, feed it to
    # _unpack_contents, and verify that _unpack_contents
    # works correctly.

    known_tree = "GM4TOORVHJTGS3DFGEWDSNJ2KVJESOSDJBFTU33MPB2GS3LZNVYG6N3GGI3WU5TIORTXC3DOMJ2G4NB2MVWXUZDONBVTE5LNGRZWK2LYN55GY23XGNYXQMTOMZUWU5TENN4DG23ZG5UTO2L2NQ2DO6LFMRWDMZJWGRQTUMZ2GEYDUMJQFQYTIMZ22XZKZORX5XS7CAQCSK3URR6QOHISHRCMGER5LRFSZRNAS5ZSALCS6TWFQAE754IVOIKJVK73WZPP3VUUEDTX3WHTBBZ5YX3CEKHCPG3ZWQLYA4QM6LDRCF7TJQYWLIZHKGN5ROA3AUZPXESBNLQQ6JTC2DBJU2D47IZJTLR3PKZ4RVF57XLPWY7FX7SZV3T6IJ3ORFW37FXUPGOE3ROPFNUX5DCGMAQJ3PGGULBRGM3TU6ZCMN2GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGI3TKNRWGEWCAITUMFUG6ZJCHIQHWITMNFXGW3LPORUW2ZJCHIQDCMRUGY3DMMZYHE3S4NBSG42TMNRRFQQCE3DJNZVWG4TUNFWWKIR2EAYTENBWGY3DGOBZG4XDIMRXGU3DMML5FQQCE3LUNFWWKIR2EAYTENBWGY3DGOBZG4XDIMRXGU3DMML5FQWDGOJRHI2TUZTJNRSTELBZGQ5FKUSJHJBUQSZ2MFYGKZ3SOBSWQ43IO52WO23CNAZWU3DUGVSWSNTIOE5DK33POVTW4ZLNMNWDK6DHPA2GS2THNF2W25DEN5VGY2LQNFRGG5DKNNRHO5TZPFTWI6LNMRYGQ2LCGJTHM4J2GM5DCMB2GQWDCNBSHKVVQBGRYMACKJ27CVQ6O6B4QPR72RFVTGOZUI76XUSWAX73JRV5PYRHMIFYZIA25MXDPGUGML6M2NMRSG4YD4W4K37ZDYSXHMJ3IUVT4F64YTQQVBJFFFOUC7J7LAB2VFCL5UKKGMR2D3F4EPOYC7UYWQZNR5KXHBSNXLCNBX2SNF22DCXJIHSMEKWEWOG5XCJEVVZ7UW5IB6I64XXQSJ34B5CAYZGZIIMR6LBRGMZTU6ZCMN2GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGMYDEMJYFQQCE5DBNBXWKIR2EB5SE3DJNZVW233UNFWWKIR2EAYTENBWGY3DGOBZG4XDIMZQGIYTQLBAEJWGS3TLMNZHI2LNMURDUIBRGI2DMNRWGM4DSNZOGQZTAMRRHB6SYIBCNV2GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGMYDEMJYPUWCYMZZGU5DKOTGNFWGKMZMHE2DUVKSJE5EGSCLHJRW25DDPBYTO2DXPB3GM6DBNYZTI6LJMV3DM2LWNB4TU4LWMNSWW3LKORXWK5DEMN3TI23NNE3WEM3SORRGY5THPA3TKNBUMNZG453BOF2GSZLXMVWWI3DJOFZW623RHIZTUMJQHI2SYMJUGI5BOSHWDPG3WKPAVXCF3XMKA7QVIWPRMWJHDTQHD27AHDCPJWDQENQ5H5ZZILTXQNIXXCIW4LKQABU2GCFRG5FHQN7CHD7HF4EKNRZFIV2ZYQIBM7IQU7F4RGB3XCX3FREPBKQ7UCICHVWPCYFGA6OLH3J45LXQ6GWWICJ3PGWJNLZ7PCRNLAPNYUGU6BENS7OXMBEOOFRIZV3PF2FFWZ5WHDPKXERYP7GNHKRMGEZTOOT3EJRXI2LNMURDUIBRGI2DMNRWGM4DSNZOGQZTGNRSGY4SYIBCORQWQ33FEI5CA6ZCNRUW423NN52GS3LFEI5CAMJSGQ3DMNRTHA4TOLRUGMZTMMRWHEWCAITMNFXGWY3SORUW2ZJCHIQDCMRUGY3DMMZYHE3S4NBTGM3DENRZPUWCAITNORUW2ZJCHIQDCMRUGY3DMMZYHE3S4NBTGM3DENRZPUWCY==="

    def test_unpack_and_pack_behavior(self):
        known_tree = b32decode(self.known_tree)
        nodemaker = NodeMaker(None, None, None,
                              None, None,
                              {"k": 3, "n": 10}, None, None)
        write_uri = "URI:SSK-RO:e3mdrzfwhoq42hy5ubcz6rp3o4:ybyibhnp3vvwuq2vaw2ckjmesgkklfs6ghxleztqidihjyofgw7q"
        filenode = nodemaker.create_from_cap(write_uri)
        node = dirnode.DirectoryNode(filenode, nodemaker, None)
        children = node._unpack_contents(known_tree)
        self._check_children(children)

        packed_children = node._pack_contents(children)
        children = node._unpack_contents(packed_children)
        self._check_children(children)

    def _check_children(self, children):
        # Are all the expected child nodes there?
        self.failUnless(children.has_key(u'file1'))
        self.failUnless(children.has_key(u'file2'))
        self.failUnless(children.has_key(u'file3'))

        # Are the metadata for child 3 right?
        file3_rocap = "URI:CHK:cmtcxq7hwxvfxan34yiev6ivhy:qvcekmjtoetdcw4kmi7b3rtblvgx7544crnwaqtiewemdliqsokq:3:10:5"
        file3_rwcap = "URI:CHK:cmtcxq7hwxvfxan34yiev6ivhy:qvcekmjtoetdcw4kmi7b3rtblvgx7544crnwaqtiewemdliqsokq:3:10:5"
        file3_metadata = {'ctime': 1246663897.4336269, 'tahoe': {'linkmotime': 1246663897.4336269, 'linkcrtime': 1246663897.4336269}, 'mtime': 1246663897.4336269}
        self.failUnlessEqual(file3_metadata, children[u'file3'][1])
        self.failUnlessReallyEqual(file3_rocap,
                                   children[u'file3'][0].get_readonly_uri())
        self.failUnlessReallyEqual(file3_rwcap,
                                   children[u'file3'][0].get_uri())

        # Are the metadata for child 2 right?
        file2_rocap = "URI:CHK:apegrpehshwugkbh3jlt5ei6hq:5oougnemcl5xgx4ijgiumtdojlipibctjkbwvyygdymdphib2fvq:3:10:4"
        file2_rwcap = "URI:CHK:apegrpehshwugkbh3jlt5ei6hq:5oougnemcl5xgx4ijgiumtdojlipibctjkbwvyygdymdphib2fvq:3:10:4"
        file2_metadata = {'ctime': 1246663897.430218, 'tahoe': {'linkmotime': 1246663897.430218, 'linkcrtime': 1246663897.430218}, 'mtime': 1246663897.430218}
        self.failUnlessEqual(file2_metadata, children[u'file2'][1])
        self.failUnlessReallyEqual(file2_rocap,
                                   children[u'file2'][0].get_readonly_uri())
        self.failUnlessReallyEqual(file2_rwcap,
                                   children[u'file2'][0].get_uri())

        # Are the metadata for child 1 right?
        file1_rocap = "URI:CHK:olxtimympo7f27jvhtgqlnbtn4:emzdnhk2um4seixozlkw3qx2nfijvdkx3ky7i7izl47yedl6e64a:3:10:10"
        file1_rwcap = "URI:CHK:olxtimympo7f27jvhtgqlnbtn4:emzdnhk2um4seixozlkw3qx2nfijvdkx3ky7i7izl47yedl6e64a:3:10:10"
        file1_metadata = {'ctime': 1246663897.4275661, 'tahoe': {'linkmotime': 1246663897.4275661, 'linkcrtime': 1246663897.4275661}, 'mtime': 1246663897.4275661}
        self.failUnlessEqual(file1_metadata, children[u'file1'][1])
        self.failUnlessReallyEqual(file1_rocap,
                                   children[u'file1'][0].get_readonly_uri())
        self.failUnlessReallyEqual(file1_rwcap,
                                   children[u'file1'][0].get_uri())

    def _make_kids(self, nm, which):
        caps = {"imm": "URI:CHK:n7r3m6wmomelk4sep3kw5cvduq:os7ijw5c3maek7pg65e5254k2fzjflavtpejjyhshpsxuqzhcwwq:3:20:14861",
                "lit": "URI:LIT:n5xgk", # LIT for "one"
                "write": "URI:SSK:vfvcbdfbszyrsaxchgevhmmlii:euw4iw7bbnkrrwpzuburbhppuxhc3gwxv26f6imekhz7zyw2ojnq",
                "read": "URI:SSK-RO:e3mdrzfwhoq42hy5ubcz6rp3o4:ybyibhnp3vvwuq2vaw2ckjmesgkklfs6ghxleztqidihjyofgw7q",
                "dirwrite": "URI:DIR2:n6x24zd3seu725yluj75q5boaa:mm6yoqjhl6ueh7iereldqxue4nene4wl7rqfjfybqrehdqmqskvq",
                "dirread":  "URI:DIR2-RO:b7sr5qsifnicca7cbk3rhrhbvq:mm6yoqjhl6ueh7iereldqxue4nene4wl7rqfjfybqrehdqmqskvq",
                }
        kids = {}
        for name in which:
            kids[unicode(name)] = (nm.create_from_cap(caps[name]), {})
        return kids

    def test_deep_immutable(self):
        nm = NodeMaker(None, None, None, None, None, {"k": 3, "n": 10}, None, None)
        fn = MinimalFakeMutableFile()

        kids = self._make_kids(nm, ["imm", "lit", "write", "read",
                                    "dirwrite", "dirread"])
        packed = dirnode.pack_children(kids, fn.get_writekey(), deep_immutable=False)
        self.failUnlessIn("lit", packed)

        kids = self._make_kids(nm, ["imm", "lit"])
        packed = dirnode.pack_children(kids, fn.get_writekey(), deep_immutable=True)
        self.failUnlessIn("lit", packed)

        kids = self._make_kids(nm, ["imm", "lit", "write"])
        self.failUnlessRaises(dirnode.MustBeDeepImmutableError,
                              dirnode.pack_children,
                              kids, fn.get_writekey(), deep_immutable=True)

        # read-only is not enough: all children must be immutable
        kids = self._make_kids(nm, ["imm", "lit", "read"])
        self.failUnlessRaises(dirnode.MustBeDeepImmutableError,
                              dirnode.pack_children,
                              kids, fn.get_writekey(), deep_immutable=True)

        kids = self._make_kids(nm, ["imm", "lit", "dirwrite"])
        self.failUnlessRaises(dirnode.MustBeDeepImmutableError,
                              dirnode.pack_children,
                              kids, fn.get_writekey(), deep_immutable=True)

        kids = self._make_kids(nm, ["imm", "lit", "dirread"])
        self.failUnlessRaises(dirnode.MustBeDeepImmutableError,
                              dirnode.pack_children,
                              kids, fn.get_writekey(), deep_immutable=True)

@implementer(IMutableFileNode)
class FakeMutableFile(object):
    counter = 0
    def __init__(self, initial_contents=""):
        data = self._get_initial_contents(initial_contents)
        self.data = data.read(data.get_size())
        self.data = "".join(self.data)

        counter = FakeMutableFile.counter
        FakeMutableFile.counter += 1
        writekey = hashutil.ssk_writekey_hash(str(counter))
        fingerprint = hashutil.ssk_pubkey_fingerprint_hash(str(counter))
        self.uri = uri.WriteableSSKFileURI(writekey, fingerprint)

    def _get_initial_contents(self, contents):
        if isinstance(contents, str):
            return contents
        if contents is None:
            return ""
        assert callable(contents), "%s should be callable, not %s" % \
               (contents, type(contents))
        return contents(self)

    def get_cap(self):
        return self.uri

    def get_uri(self):
        return self.uri.to_string()

    def get_write_uri(self):
        return self.uri.to_string()

    def download_best_version(self, progress=None):
        return defer.succeed(self.data)

    def get_writekey(self):
        return "writekey"

    def is_readonly(self):
        return False

    def is_mutable(self):
        return True

    def is_unknown(self):
        return False

    def is_allowed_in_immutable_directory(self):
        return False

    def raise_error(self):
        pass

    def modify(self, modifier):
        data = modifier(self.data, None, True)
        self.data = data
        return defer.succeed(None)

class FakeNodeMaker(NodeMaker):
    def create_mutable_file(self, contents="", keysize=None, version=None):
        return defer.succeed(FakeMutableFile(contents))

class FakeClient2(_Client):
    def __init__(self):
        self.nodemaker = FakeNodeMaker(None, None, None,
                                       None, None,
                                       {"k":3,"n":10}, None, None)
    def create_node_from_uri(self, rwcap, rocap):
        return self.nodemaker.create_from_cap(rwcap, rocap)

class Dirnode2(testutil.ReallyEqualMixin, testutil.ShouldFailMixin, unittest.TestCase):
    def setUp(self):
        client = FakeClient2()
        self.nodemaker = client.nodemaker

    def test_from_future(self):
        # Create a mutable directory that contains unknown URI types, and make sure
        # we tolerate them properly.
        d = self.nodemaker.create_new_mutable_directory()
        future_write_uri = u"x-tahoe-crazy://I_am_from_the_future_rw_\u263A".encode('utf-8')
        future_read_uri = u"x-tahoe-crazy-readonly://I_am_from_the_future_ro_\u263A".encode('utf-8')
        future_imm_uri = u"x-tahoe-crazy-immutable://I_am_from_the_future_imm_\u263A".encode('utf-8')
        future_node = UnknownNode(future_write_uri, future_read_uri)
        def _then(n):
            self._node = n
            return n.set_node(u"future", future_node)
        d.addCallback(_then)

        # We should be prohibited from adding an unknown URI to a directory
        # just in the rw_uri slot, since we don't know how to diminish the cap
        # to a readcap (for the ro_uri slot).
        d.addCallback(lambda ign:
             self.shouldFail(MustNotBeUnknownRWError,
                             "copy unknown",
                             "cannot attach unknown rw cap as child",
                             self._node.set_uri, u"add",
                             future_write_uri, None))

        # However, we should be able to add both rw_uri and ro_uri as a pair of
        # unknown URIs.
        d.addCallback(lambda ign: self._node.set_uri(u"add-pair",
                                                     future_write_uri, future_read_uri))

        # and to add an URI prefixed with "ro." or "imm." when it is given in a
        # write slot (or URL parameter).
        d.addCallback(lambda ign: self._node.set_uri(u"add-ro",
                                                     "ro." + future_read_uri, None))
        d.addCallback(lambda ign: self._node.set_uri(u"add-imm",
                                                     "imm." + future_imm_uri, None))

        d.addCallback(lambda ign: self._node.list())
        def _check(children):
            self.failUnlessReallyEqual(len(children), 4)
            (fn, metadata) = children[u"future"]
            self.failUnless(isinstance(fn, UnknownNode), fn)
            self.failUnlessReallyEqual(fn.get_uri(), future_write_uri)
            self.failUnlessReallyEqual(fn.get_write_uri(), future_write_uri)
            self.failUnlessReallyEqual(fn.get_readonly_uri(), "ro." + future_read_uri)

            (fn2, metadata2) = children[u"add-pair"]
            self.failUnless(isinstance(fn2, UnknownNode), fn2)
            self.failUnlessReallyEqual(fn2.get_uri(), future_write_uri)
            self.failUnlessReallyEqual(fn2.get_write_uri(), future_write_uri)
            self.failUnlessReallyEqual(fn2.get_readonly_uri(), "ro." + future_read_uri)

            (fn3, metadata3) = children[u"add-ro"]
            self.failUnless(isinstance(fn3, UnknownNode), fn3)
            self.failUnlessReallyEqual(fn3.get_uri(), "ro." + future_read_uri)
            self.failUnlessReallyEqual(fn3.get_write_uri(), None)
            self.failUnlessReallyEqual(fn3.get_readonly_uri(), "ro." + future_read_uri)

            (fn4, metadata4) = children[u"add-imm"]
            self.failUnless(isinstance(fn4, UnknownNode), fn4)
            self.failUnlessReallyEqual(fn4.get_uri(), "imm." + future_imm_uri)
            self.failUnlessReallyEqual(fn4.get_write_uri(), None)
            self.failUnlessReallyEqual(fn4.get_readonly_uri(), "imm." + future_imm_uri)

            # We should also be allowed to copy the "future" UnknownNode, because
            # it contains all the information that was in the original directory
            # (readcap and writecap), so we're preserving everything.
            return self._node.set_node(u"copy", fn)
        d.addCallback(_check)

        d.addCallback(lambda ign: self._node.list())
        def _check2(children):
            self.failUnlessReallyEqual(len(children), 5)
            (fn, metadata) = children[u"copy"]
            self.failUnless(isinstance(fn, UnknownNode), fn)
            self.failUnlessReallyEqual(fn.get_uri(), future_write_uri)
            self.failUnlessReallyEqual(fn.get_write_uri(), future_write_uri)
            self.failUnlessReallyEqual(fn.get_readonly_uri(), "ro." + future_read_uri)
        d.addCallback(_check2)
        return d

    def test_unknown_strip_prefix_for_ro(self):
        self.failUnlessReallyEqual(strip_prefix_for_ro("foo",     False), "foo")
        self.failUnlessReallyEqual(strip_prefix_for_ro("ro.foo",  False), "foo")
        self.failUnlessReallyEqual(strip_prefix_for_ro("imm.foo", False), "imm.foo")
        self.failUnlessReallyEqual(strip_prefix_for_ro("foo",     True),  "foo")
        self.failUnlessReallyEqual(strip_prefix_for_ro("ro.foo",  True),  "foo")
        self.failUnlessReallyEqual(strip_prefix_for_ro("imm.foo", True),  "foo")

    def test_unknownnode(self):
        lit_uri = one_uri

        # This does not attempt to be exhaustive.
        no_no        = [# Opaque node, but not an error.
                        ( 0, UnknownNode(None, None)),
                        ( 1, UnknownNode(None, None, deep_immutable=True)),
                       ]
        unknown_rw   = [# These are errors because we're only given a rw_uri, and we can't
                        # diminish it.
                        ( 2, UnknownNode("foo", None)),
                        ( 3, UnknownNode("foo", None, deep_immutable=True)),
                        ( 4, UnknownNode("ro.foo", None, deep_immutable=True)),
                        ( 5, UnknownNode("ro." + mut_read_uri, None, deep_immutable=True)),
                        ( 5.1, UnknownNode("ro." + mdmf_read_uri, None, deep_immutable=True)),
                        ( 6, UnknownNode("URI:SSK-RO:foo", None, deep_immutable=True)),
                        ( 7, UnknownNode("URI:SSK:foo", None)),
                       ]
        must_be_ro   = [# These are errors because a readonly constraint is not met.
                        ( 8, UnknownNode("ro." + mut_write_uri, None)),
                        ( 8.1, UnknownNode("ro." + mdmf_write_uri, None)),
                        ( 9, UnknownNode(None, "ro." + mut_write_uri)),
                        ( 9.1, UnknownNode(None, "ro." + mdmf_write_uri)),
                       ]
        must_be_imm  = [# These are errors because an immutable constraint is not met.
                        (10, UnknownNode(None, "ro.URI:SSK-RO:foo", deep_immutable=True)),
                        (11, UnknownNode(None, "imm.URI:SSK:foo")),
                        (12, UnknownNode(None, "imm.URI:SSK-RO:foo")),
                        (13, UnknownNode("bar", "ro.foo", deep_immutable=True)),
                        (14, UnknownNode("bar", "imm.foo", deep_immutable=True)),
                        (15, UnknownNode("bar", "imm." + lit_uri, deep_immutable=True)),
                        (16, UnknownNode("imm." + mut_write_uri, None)),
                        (16.1, UnknownNode("imm." + mdmf_write_uri, None)),
                        (17, UnknownNode("imm." + mut_read_uri, None)),
                        (17.1, UnknownNode("imm." + mdmf_read_uri, None)),
                        (18, UnknownNode("bar", "imm.foo")),
                       ]
        bad_uri      = [# These are errors because the URI is bad once we've stripped the prefix.
                        (19, UnknownNode("ro.URI:SSK-RO:foo", None)),
                        (20, UnknownNode("imm.URI:CHK:foo", None, deep_immutable=True)),
                        (21, UnknownNode(None, "URI:CHK:foo")),
                        (22, UnknownNode(None, "URI:CHK:foo", deep_immutable=True)),
                       ]
        ro_prefixed  = [# These are valid, and the readcap should end up with a ro. prefix.
                        (23, UnknownNode(None, "foo")),
                        (24, UnknownNode(None, "ro.foo")),
                        (25, UnknownNode(None, "ro." + lit_uri)),
                        (26, UnknownNode("bar", "foo")),
                        (27, UnknownNode("bar", "ro.foo")),
                        (28, UnknownNode("bar", "ro." + lit_uri)),
                        (29, UnknownNode("ro.foo", None)),
                        (30, UnknownNode("ro." + lit_uri, None)),
                       ]
        imm_prefixed = [# These are valid, and the readcap should end up with an imm. prefix.
                        (31, UnknownNode(None, "foo", deep_immutable=True)),
                        (32, UnknownNode(None, "ro.foo", deep_immutable=True)),
                        (33, UnknownNode(None, "imm.foo")),
                        (34, UnknownNode(None, "imm.foo", deep_immutable=True)),
                        (35, UnknownNode("imm." + lit_uri, None)),
                        (36, UnknownNode("imm." + lit_uri, None, deep_immutable=True)),
                        (37, UnknownNode(None, "imm." + lit_uri)),
                        (38, UnknownNode(None, "imm." + lit_uri, deep_immutable=True)),
                       ]
        error = unknown_rw + must_be_ro + must_be_imm + bad_uri
        ok = ro_prefixed + imm_prefixed

        for (i, n) in no_no + error + ok:
            self.failUnless(n.is_unknown(), i)

        for (i, n) in no_no + error:
            self.failUnless(n.get_uri() is None, i)
            self.failUnless(n.get_write_uri() is None, i)
            self.failUnless(n.get_readonly_uri() is None, i)

        for (i, n) in no_no + ok:
            n.raise_error()

        for (i, n) in unknown_rw:
            self.failUnlessRaises(MustNotBeUnknownRWError, lambda n=n: n.raise_error())

        for (i, n) in must_be_ro:
            self.failUnlessRaises(MustBeReadonlyError, lambda n=n: n.raise_error())

        for (i, n) in must_be_imm:
            self.failUnlessRaises(MustBeDeepImmutableError, lambda n=n: n.raise_error())

        for (i, n) in bad_uri:
            self.failUnlessRaises(uri.BadURIError, lambda n=n: n.raise_error())

        for (i, n) in ok:
            self.failIf(n.get_readonly_uri() is None, i)

        for (i, n) in ro_prefixed:
            self.failUnless(n.get_readonly_uri().startswith("ro."), i)

        for (i, n) in imm_prefixed:
            self.failUnless(n.get_readonly_uri().startswith("imm."), i)



class DeepStats(testutil.ReallyEqualMixin, unittest.TestCase):
    def test_stats(self):
        ds = dirnode.DeepStats(None)
        ds.add("count-files")
        ds.add("size-immutable-files", 123)
        ds.histogram("size-files-histogram", 123)
        ds.max("largest-directory", 444)

        s = ds.get_results()
        self.failUnlessReallyEqual(s["count-files"], 1)
        self.failUnlessReallyEqual(s["size-immutable-files"], 123)
        self.failUnlessReallyEqual(s["largest-directory"], 444)
        self.failUnlessReallyEqual(s["count-literal-files"], 0)

        ds.add("count-files")
        ds.add("size-immutable-files", 321)
        ds.histogram("size-files-histogram", 321)
        ds.max("largest-directory", 2)

        s = ds.get_results()
        self.failUnlessReallyEqual(s["count-files"], 2)
        self.failUnlessReallyEqual(s["size-immutable-files"], 444)
        self.failUnlessReallyEqual(s["largest-directory"], 444)
        self.failUnlessReallyEqual(s["count-literal-files"], 0)
        self.failUnlessReallyEqual(s["size-files-histogram"],
                                   [ (101, 316, 1), (317, 1000, 1) ])

        ds = dirnode.DeepStats(None)
        for i in range(1, 1100):
            ds.histogram("size-files-histogram", i)
        ds.histogram("size-files-histogram", 4*1000*1000*1000*1000) # 4TB
        s = ds.get_results()
        self.failUnlessReallyEqual(s["size-files-histogram"],
                                   [ (1, 3, 3),
                                     (4, 10, 7),
                                     (11, 31, 21),
                                     (32, 100, 69),
                                     (101, 316, 216),
                                     (317, 1000, 684),
                                     (1001, 3162, 99),
                                     (long(3162277660169), long(10000000000000), 1),
                                     ])

class UCWEingMutableFileNode(MutableFileNode):
    please_ucwe_after_next_upload = False

    def _upload(self, new_contents, servermap):
        d = MutableFileNode._upload(self, new_contents, servermap)
        def _ucwe(res):
            if self.please_ucwe_after_next_upload:
                self.please_ucwe_after_next_upload = False
                raise UncoordinatedWriteError()
            return res
        d.addCallback(_ucwe)
        return d

class UCWEingNodeMaker(NodeMaker):
    def _create_mutable(self, cap):
        n = UCWEingMutableFileNode(self.storage_broker, self.secret_holder,
                                   self.default_encoding_parameters,
                                   self.history)
        return n.init_from_cap(cap)


class Deleter(GridTestMixin, testutil.ReallyEqualMixin, unittest.TestCase):
    def test_retry(self):
        # ticket #550, a dirnode.delete which experiences an
        # UncoordinatedWriteError will fail with an incorrect "you're
        # deleting something which isn't there" NoSuchChildError exception.

        # to trigger this, we start by creating a directory with a single
        # file in it. Then we create a special dirnode that uses a modified
        # MutableFileNode which will raise UncoordinatedWriteError once on
        # demand. We then call dirnode.delete, which ought to retry and
        # succeed.

        self.basedir = self.mktemp()
        self.set_up_grid(oneshare=True)
        c0 = self.g.clients[0]
        d = c0.create_dirnode()
        small = upload.Data("Small enough for a LIT", None)
        def _created_dir(dn):
            self.root = dn
            self.root_uri = dn.get_uri()
            return dn.add_file(u"file", small)
        d.addCallback(_created_dir)
        def _do_delete(ignored):
            nm = UCWEingNodeMaker(c0.storage_broker, c0._secret_holder,
                                  c0.get_history(), c0.getServiceNamed("uploader"),
                                  c0.terminator,
                                  c0.get_encoding_parameters(),
                                  c0.mutable_file_default,
                                  c0._key_generator)
            n = nm.create_from_cap(self.root_uri)
            assert n._node.please_ucwe_after_next_upload == False
            n._node.please_ucwe_after_next_upload = True
            # This should succeed, not raise an exception
            return n.delete(u"file")
        d.addCallback(_do_delete)

        return d

class Adder(GridTestMixin, unittest.TestCase, testutil.ShouldFailMixin):

    def test_overwrite(self):
        # note: This functionality could be tested without actually creating
        # several RSA keys. It would be faster without the GridTestMixin: use
        # dn.set_node(nodemaker.create_from_cap(make_chk_file_uri())) instead
        # of dn.add_file, and use a special NodeMaker that creates fake
        # mutable files.
        self.basedir = "dirnode/Adder/test_overwrite"
        self.set_up_grid(oneshare=True)
        c = self.g.clients[0]
        fileuri = make_chk_file_uri(1234)
        filenode = c.nodemaker.create_from_cap(fileuri)
        d = c.create_dirnode()

        def _create_directory_tree(root_node):
            # Build
            # root/file1
            # root/file2
            # root/dir1
            d = root_node.add_file(u'file1', upload.Data("Important Things",
                None))
            d.addCallback(lambda res:
                root_node.add_file(u'file2', upload.Data("Sekrit Codes", None)))
            d.addCallback(lambda res:
                root_node.create_subdirectory(u"dir1"))
            d.addCallback(lambda res: root_node)
            return d

        d.addCallback(_create_directory_tree)

        def _test_adder(root_node):
            d = root_node.set_node(u'file1', filenode)
            # We've overwritten file1. Let's try it with a directory
            d.addCallback(lambda res:
                root_node.create_subdirectory(u'dir2'))
            d.addCallback(lambda res:
                root_node.set_node(u'dir2', filenode))
            # We try overwriting a file with a child while also specifying
            # overwrite=False. We should receive an ExistingChildError
            # when we do this.
            d.addCallback(lambda res:
                self.shouldFail(ExistingChildError, "set_node",
                                "child 'file1' already exists",
                                root_node.set_node, u"file1",
                                filenode, overwrite=False))
            # If we try with a directory, we should see the same thing
            d.addCallback(lambda res:
                self.shouldFail(ExistingChildError, "set_node",
                                "child 'dir1' already exists",
                                root_node.set_node, u'dir1', filenode,
                                overwrite=False))
            d.addCallback(lambda res:
                root_node.set_node(u'file1', filenode,
                                   overwrite="only-files"))
            d.addCallback(lambda res:
                self.shouldFail(ExistingChildError, "set_node",
                                "child 'dir1' already exists",
                                root_node.set_node, u'dir1', filenode,
                                overwrite="only-files"))
            return d

        d.addCallback(_test_adder)
        return d
