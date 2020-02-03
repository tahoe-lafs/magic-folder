# -*- test-case-name: allmydata.test.test_encode -*-

import time
from zope.interface import implementer
from twisted.internet import defer
from foolscap.api import fireEventually
from allmydata import uri
from allmydata.storage.server import si_b2a
from allmydata.hashtree import HashTree
from allmydata.util import mathutil, hashutil, base32, log, happinessutil
from allmydata.util.assertutil import _assert, precondition
from allmydata.codec import CRSEncoder
from allmydata.interfaces import IEncoder, IStorageBucketWriter, \
     IEncryptedUploadable, IUploadStatus, UploadUnhappinessError

from ..util.eliotutil import (
    log_call_deferred,
)

"""
The goal of the encoder is to turn the original file into a series of
'shares'. Each share is going to a 'shareholder' (nominally each shareholder
is a different host, but for small grids there may be overlap). The number
of shares is chosen to hit our reliability goals (more shares on more
machines means more reliability), and is limited by overhead (proportional to
numshares or log(numshares)) and the encoding technology in use (zfec permits
only 256 shares total). It is also constrained by the amount of data
we want to send to each host. For estimating purposes, think of 10 shares
out of which we need 3 to reconstruct the file.

The encoder starts by cutting the original file into segments. All segments
except the last are of equal size. The segment size is chosen to constrain
the memory footprint (which will probably vary between 1x and 4x segment
size) and to constrain the overhead (which will be proportional to
log(number of segments)).


Each segment (A,B,C) is read into memory, encrypted, and encoded into
blocks. The 'share' (say, share #1) that makes it out to a host is a
collection of these blocks (block A1, B1, C1), plus some hash-tree
information necessary to validate the data upon retrieval. Only one segment
is handled at a time: all blocks for segment A are delivered before any
work is begun on segment B.

As blocks are created, we retain the hash of each one. The list of block hashes
for a single share (say, hash(A1), hash(B1), hash(C1)) is used to form the base
of a Merkle hash tree for that share, called the block hash tree.

This hash tree has one terminal leaf per block. The complete block hash
tree is sent to the shareholder after all the data has been sent. At
retrieval time, the decoder will ask for specific pieces of this tree before
asking for blocks, whichever it needs to validate those blocks.

(Note: we don't really need to generate this whole block hash tree
ourselves. It would be sufficient to have the shareholder generate it and
just tell us the root. This gives us an extra level of validation on the
transfer, though, and it is relatively cheap to compute.)

Each of these block hash trees has a root hash. The collection of these
root hashes for all shares are collected into the 'share hash tree', which
has one terminal leaf per share. After sending the blocks and the complete
block hash tree to each shareholder, we send them the portion of the share
hash tree that is necessary to validate their share. The root of the share
hash tree is put into the URI.

"""

class UploadAborted(Exception):
    pass

KiB=1024
MiB=1024*KiB
GiB=1024*MiB
TiB=1024*GiB
PiB=1024*TiB

@implementer(IEncoder)
class Encoder(object):

    def __init__(self, log_parent=None, upload_status=None, progress=None):
        object.__init__(self)
        self.uri_extension_data = {}
        self._codec = None
        self._status = None
        if upload_status:
            self._status = IUploadStatus(upload_status)
        precondition(log_parent is None or isinstance(log_parent, int),
                     log_parent)
        self._log_number = log.msg("creating Encoder %s" % self,
                                   facility="tahoe.encoder", parent=log_parent)
        self._aborted = False
        self._progress = progress

    def __repr__(self):
        if hasattr(self, "_storage_index"):
            return "<Encoder for %s>" % si_b2a(self._storage_index)[:5]
        return "<Encoder for unknown storage index>"

    def log(self, *args, **kwargs):
        if "parent" not in kwargs:
            kwargs["parent"] = self._log_number
        if "facility" not in kwargs:
            kwargs["facility"] = "tahoe.encoder"
        return log.msg(*args, **kwargs)

    @log_call_deferred(action_type=u"immutable:encode:set-encrypted-uploadable")
    def set_encrypted_uploadable(self, uploadable):
        eu = self._uploadable = IEncryptedUploadable(uploadable)
        d = eu.get_size()
        def _got_size(size):
            self.log(format="file size: %(size)d", size=size)
            self.file_size = size
            if self._progress:
                self._progress.set_progress_total(self.file_size)
        d.addCallback(_got_size)
        d.addCallback(lambda res: eu.get_all_encoding_parameters())
        d.addCallback(self._got_all_encoding_parameters)
        d.addCallback(lambda res: eu.get_storage_index())
        def _done(storage_index):
            self._storage_index = storage_index
            return self
        d.addCallback(_done)
        return d

    def _got_all_encoding_parameters(self, params):
        assert not self._codec
        k, happy, n, segsize = params
        self.required_shares = k
        self.min_happiness = happy
        self.num_shares = n
        self.segment_size = segsize
        self.log("got encoding parameters: %d/%d/%d %d" % (k,happy,n, segsize))
        self.log("now setting up codec")

        assert self.segment_size % self.required_shares == 0

        self.num_segments = mathutil.div_ceil(self.file_size,
                                              self.segment_size)

        self._codec = CRSEncoder()
        self._codec.set_params(self.segment_size,
                               self.required_shares, self.num_shares)

        data = self.uri_extension_data
        data['codec_name'] = self._codec.get_encoder_type()
        data['codec_params'] = self._codec.get_serialized_params()

        data['size'] = self.file_size
        data['segment_size'] = self.segment_size
        self.share_size = mathutil.div_ceil(self.file_size,
                                            self.required_shares)
        data['num_segments'] = self.num_segments
        data['needed_shares'] = self.required_shares
        data['total_shares'] = self.num_shares

        # the "tail" is the last segment. This segment may or may not be
        # shorter than all other segments. We use the "tail codec" to handle
        # it. If the tail is short, we use a different codec instance. In
        # addition, the tail codec must be fed data which has been padded out
        # to the right size.
        tail_size = self.file_size % self.segment_size
        if not tail_size:
            tail_size = self.segment_size

        # the tail codec is responsible for encoding tail_size bytes
        padded_tail_size = mathutil.next_multiple(tail_size,
                                                  self.required_shares)
        self._tail_codec = CRSEncoder()
        self._tail_codec.set_params(padded_tail_size,
                                    self.required_shares, self.num_shares)
        data['tail_codec_params'] = self._tail_codec.get_serialized_params()

    def _get_share_size(self):
        share_size = mathutil.div_ceil(self.file_size, self.required_shares)
        overhead = self._compute_overhead()
        return share_size + overhead

    def _compute_overhead(self):
        return 0

    def get_param(self, name):
        assert self._codec

        if name == "storage_index":
            return self._storage_index
        elif name == "share_counts":
            return (self.required_shares, self.min_happiness,
                    self.num_shares)
        elif name == "num_segments":
            return self.num_segments
        elif name == "segment_size":
            return self.segment_size
        elif name == "block_size":
            return self._codec.get_block_size()
        elif name == "share_size":
            return self._get_share_size()
        elif name == "serialized_params":
            return self._codec.get_serialized_params()
        else:
            raise KeyError("unknown parameter name '%s'" % name)

    def set_shareholders(self, landlords, servermap):
        assert isinstance(landlords, dict)
        for k in landlords:
            assert IStorageBucketWriter.providedBy(landlords[k])
        self.landlords = landlords.copy()
        assert isinstance(servermap, dict)
        for v in servermap.itervalues():
            assert isinstance(v, set)
        self.servermap = servermap.copy()

    @log_call_deferred(action_type=u"immutable:encode:start")
    def start(self):
        """ Returns a Deferred that will fire with the verify cap (an instance of
        uri.CHKFileVerifierURI)."""
        self.log("%s starting" % (self,))
        #paddedsize = self._size + mathutil.pad_size(self._size, self.needed_shares)
        assert self._codec
        self._crypttext_hasher = hashutil.crypttext_hasher()
        self._crypttext_hashes = []
        self.segment_num = 0
        self.block_hashes = [[] for x in range(self.num_shares)]
        # block_hashes[i] is a list that will be accumulated and then send
        # to landlord[i]. This list contains a hash of each segment_share
        # that we sent to that landlord.
        self.share_root_hashes = [None] * self.num_shares

        self._times = {
            "cumulative_encoding": 0.0,
            "cumulative_sending": 0.0,
            "hashes_and_close": 0.0,
            "total_encode_and_push": 0.0,
            }
        self._start_total_timestamp = time.time()

        d = fireEventually()

        d.addCallback(lambda res: self.start_all_shareholders())

        for i in range(self.num_segments-1):
            # note to self: this form doesn't work, because lambda only
            # captures the slot, not the value
            #d.addCallback(lambda res: self.do_segment(i))
            # use this form instead:
            d.addCallback(lambda res, i=i: self._encode_segment(i))
            d.addCallback(self._send_segment, i)
            d.addCallback(self._turn_barrier)
        last_segnum = self.num_segments - 1
        d.addCallback(lambda res: self._encode_tail_segment(last_segnum))
        d.addCallback(self._send_segment, last_segnum)
        d.addCallback(self._turn_barrier)

        d.addCallback(lambda res: self.finish_hashing())

        d.addCallback(lambda res:
                      self.send_crypttext_hash_tree_to_all_shareholders())
        d.addCallback(lambda res: self.send_all_block_hash_trees())
        d.addCallback(lambda res: self.send_all_share_hash_trees())
        d.addCallback(lambda res: self.send_uri_extension_to_all_shareholders())

        d.addCallback(lambda res: self.close_all_shareholders())
        d.addCallbacks(self.done, self.err)
        return d

    def set_status(self, status):
        if self._status:
            self._status.set_status(status)

    def set_encode_and_push_progress(self, sent_segments=None, extra=0.0):
        if self._status:
            # we treat the final hash+close as an extra segment
            if sent_segments is None:
                sent_segments = self.num_segments
            progress = float(sent_segments + extra) / (self.num_segments + 1)
            self._status.set_progress(2, progress)

    def abort(self):
        self.log("aborting upload", level=log.UNUSUAL)
        assert self._codec, "don't call abort before start"
        self._aborted = True
        # the next segment read (in _gather_data inside _encode_segment) will
        # raise UploadAborted(), which will bypass the rest of the upload
        # chain. If we've sent the final segment's shares, it's too late to
        # abort. TODO: allow abort any time up to close_all_shareholders.

    def _turn_barrier(self, res):
        # putting this method in a Deferred chain imposes a guaranteed
        # reactor turn between the pre- and post- portions of that chain.
        # This can be useful to limit memory consumption: since Deferreds do
        # not do tail recursion, code which uses defer.succeed(result) for
        # consistency will cause objects to live for longer than you might
        # normally expect.

        return fireEventually(res)


    def start_all_shareholders(self):
        self.log("starting shareholders", level=log.NOISY)
        self.set_status("Starting shareholders")
        dl = []
        for shareid in list(self.landlords):
            d = self.landlords[shareid].put_header()
            d.addErrback(self._remove_shareholder, shareid, "start")
            dl.append(d)
        return self._gather_responses(dl)

    def _encode_segment(self, segnum):
        codec = self._codec
        start = time.time()

        # the ICodecEncoder API wants to receive a total of self.segment_size
        # bytes on each encode() call, broken up into a number of
        # identically-sized pieces. Due to the way the codec algorithm works,
        # these pieces need to be the same size as the share which the codec
        # will generate. Therefore we must feed it with input_piece_size that
        # equals the output share size.
        input_piece_size = codec.get_block_size()

        # as a result, the number of input pieces per encode() call will be
        # equal to the number of required shares with which the codec was
        # constructed. You can think of the codec as chopping up a
        # 'segment_size' of data into 'required_shares' shares (not doing any
        # fancy math at all, just doing a split), then creating some number
        # of additional shares which can be substituted if the primary ones
        # are unavailable

        # we read data from the source one segment at a time, and then chop
        # it into 'input_piece_size' pieces before handing it to the codec

        crypttext_segment_hasher = hashutil.crypttext_segment_hasher()

        # memory footprint: we only hold a tiny piece of the plaintext at any
        # given time. We build up a segment's worth of cryptttext, then hand
        # it to the encoder. Assuming 3-of-10 encoding (3.3x expansion) and
        # 1MiB max_segment_size, we get a peak memory footprint of 4.3*1MiB =
        # 4.3MiB. Lowering max_segment_size to, say, 100KiB would drop the
        # footprint to 430KiB at the expense of more hash-tree overhead.

        d = self._gather_data(self.required_shares, input_piece_size,
                              crypttext_segment_hasher)
        def _done_gathering(chunks):
            for c in chunks:
                assert len(c) == input_piece_size
            self._crypttext_hashes.append(crypttext_segment_hasher.digest())
            # during this call, we hit 5*segsize memory
            return codec.encode(chunks)
        d.addCallback(_done_gathering)
        def _done(res):
            elapsed = time.time() - start
            self._times["cumulative_encoding"] += elapsed
            return res
        d.addCallback(_done)
        return d

    def _encode_tail_segment(self, segnum):

        start = time.time()
        codec = self._tail_codec
        input_piece_size = codec.get_block_size()

        crypttext_segment_hasher = hashutil.crypttext_segment_hasher()

        d = self._gather_data(self.required_shares, input_piece_size,
                              crypttext_segment_hasher, allow_short=True)
        def _done_gathering(chunks):
            for c in chunks:
                # a short trailing chunk will have been padded by
                # _gather_data
                assert len(c) == input_piece_size
            self._crypttext_hashes.append(crypttext_segment_hasher.digest())
            return codec.encode(chunks)
        d.addCallback(_done_gathering)
        def _done(res):
            elapsed = time.time() - start
            self._times["cumulative_encoding"] += elapsed
            return res
        d.addCallback(_done)
        return d

    def _gather_data(self, num_chunks, input_chunk_size,
                     crypttext_segment_hasher,
                     allow_short=False):
        """Return a Deferred that will fire when the required number of
        chunks have been read (and hashed and encrypted). The Deferred fires
        with a list of chunks, each of size input_chunk_size."""

        # I originally built this to allow read_encrypted() to behave badly:
        # to let it return more or less data than you asked for. It would
        # stash the leftovers until later, and then recurse until it got
        # enough. I don't think that was actually useful.
        #
        # who defines read_encrypted?
        #  offloaded.LocalCiphertextReader: real disk file: exact
        #  upload.EncryptAnUploadable: Uploadable, but a wrapper that makes
        #    it exact. The return value is a list of 50KiB chunks, to reduce
        #    the memory footprint of the encryption process.
        #  repairer.Repairer: immutable.filenode.CiphertextFileNode: exact
        #
        # This has been redefined to require read_encrypted() to behave like
        # a local file: return exactly the amount requested unless it hits
        # EOF.
        #  -warner

        if self._aborted:
            raise UploadAborted()

        read_size = num_chunks * input_chunk_size
        d = self._uploadable.read_encrypted(read_size, hash_only=False)
        def _got(data):
            assert isinstance(data, (list,tuple))
            if self._aborted:
                raise UploadAborted()
            data = "".join(data)
            precondition(len(data) <= read_size, len(data), read_size)
            if not allow_short:
                precondition(len(data) == read_size, len(data), read_size)
            crypttext_segment_hasher.update(data)
            self._crypttext_hasher.update(data)
            if allow_short and len(data) < read_size:
                # padding
                data += "\x00" * (read_size - len(data))
            encrypted_pieces = [data[i:i+input_chunk_size]
                                for i in range(0, len(data), input_chunk_size)]
            return encrypted_pieces
        d.addCallback(_got)
        return d

    def _send_segment(self, shares_and_shareids, segnum):
        # To generate the URI, we must generate the roothash, so we must
        # generate all shares, even if we aren't actually giving them to
        # anybody. This means that the set of shares we create will be equal
        # to or larger than the set of landlords. If we have any landlord who
        # *doesn't* have a share, that's an error.
        (shares, shareids) = shares_and_shareids
        _assert(set(self.landlords.keys()).issubset(set(shareids)),
                shareids=shareids, landlords=self.landlords)
        start = time.time()
        dl = []
        self.set_status("Sending segment %d of %d" % (segnum+1,
                                                      self.num_segments))
        self.set_encode_and_push_progress(segnum)
        lognum = self.log("send_segment(%d)" % segnum, level=log.NOISY)
        for i in range(len(shares)):
            block = shares[i]
            shareid = shareids[i]
            d = self.send_block(shareid, segnum, block, lognum)
            dl.append(d)

            block_hash = hashutil.block_hash(block)
            #from allmydata.util import base32
            #log.msg("creating block (shareid=%d, blocknum=%d) "
            #        "len=%d %r .. %r: %s" %
            #        (shareid, segnum, len(block),
            #         block[:50], block[-50:], base32.b2a(block_hash)))
            self.block_hashes[shareid].append(block_hash)

        dl = self._gather_responses(dl)

        def do_progress(ign):
            done = self.segment_size * (segnum + 1)
            if self._progress:
                self._progress.set_progress(done)
            return ign
        dl.addCallback(do_progress)

        def _logit(res):
            self.log("%s uploaded %s / %s bytes (%d%%) of your file." %
                     (self,
                      self.segment_size*(segnum+1),
                      self.segment_size*self.num_segments,
                      100 * (segnum+1) / self.num_segments,
                      ),
                     level=log.OPERATIONAL)
            elapsed = time.time() - start
            self._times["cumulative_sending"] += elapsed
            return res
        dl.addCallback(_logit)
        return dl

    def send_block(self, shareid, segment_num, block, lognum):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        lognum2 = self.log("put_block to %s" % self.landlords[shareid],
                           parent=lognum, level=log.NOISY)
        d = sh.put_block(segment_num, block)
        def _done(res):
            self.log("put_block done", parent=lognum2, level=log.NOISY)
            return res
        d.addCallback(_done)
        d.addErrback(self._remove_shareholder, shareid,
                     "segnum=%d" % segment_num)
        return d

    def _remove_shareholder(self, why, shareid, where):
        ln = self.log(format="error while sending %(method)s to shareholder=%(shnum)d",
                      method=where, shnum=shareid,
                      level=log.UNUSUAL, failure=why)
        if shareid in self.landlords:
            self.landlords[shareid].abort()
            peerid = self.landlords[shareid].get_peerid()
            assert peerid
            del self.landlords[shareid]
            self.servermap[shareid].remove(peerid)
            if not self.servermap[shareid]:
                del self.servermap[shareid]
        else:
            # even more UNUSUAL
            self.log("they weren't in our list of landlords", parent=ln,
                     level=log.WEIRD, umid="TQGFRw")
        happiness = happinessutil.servers_of_happiness(self.servermap)
        if happiness < self.min_happiness:
            peerids = set(happinessutil.shares_by_server(self.servermap).keys())
            msg = happinessutil.failure_message(len(peerids),
                                                self.required_shares,
                                                self.min_happiness,
                                                happiness)
            msg = "%s: %s" % (msg, why)
            raise UploadUnhappinessError(msg)
        self.log("but we can still continue with %s shares, we'll be happy "
                 "with at least %s" % (happiness,
                                       self.min_happiness),
                 parent=ln)

    def _gather_responses(self, dl):
        d = defer.DeferredList(dl, fireOnOneErrback=True)
        def _eatUploadUnhappinessError(f):
            # all exceptions that occur while talking to a peer are handled
            # in _remove_shareholder. That might raise UploadUnhappinessError,
            # which will cause the DeferredList to errback but which should
            # otherwise be consumed. Allow non-UploadUnhappinessError exceptions
            # to pass through as an unhandled errback. We use this in lieu of
            # consumeErrors=True to allow coding errors to be logged.
            f.trap(UploadUnhappinessError)
            return None
        for d0 in dl:
            d0.addErrback(_eatUploadUnhappinessError)
        return d

    def finish_hashing(self):
        self._start_hashing_and_close_timestamp = time.time()
        self.set_status("Finishing hashes")
        self.set_encode_and_push_progress(extra=0.0)
        crypttext_hash = self._crypttext_hasher.digest()
        self.uri_extension_data["crypttext_hash"] = crypttext_hash
        self._uploadable.close()

    def send_crypttext_hash_tree_to_all_shareholders(self):
        self.log("sending crypttext hash tree", level=log.NOISY)
        self.set_status("Sending Crypttext Hash Tree")
        self.set_encode_and_push_progress(extra=0.3)
        t = HashTree(self._crypttext_hashes)
        all_hashes = list(t)
        self.uri_extension_data["crypttext_root_hash"] = t[0]
        dl = []
        for shareid in list(self.landlords):
            dl.append(self.send_crypttext_hash_tree(shareid, all_hashes))
        return self._gather_responses(dl)

    def send_crypttext_hash_tree(self, shareid, all_hashes):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.put_crypttext_hashes(all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_crypttext_hashes")
        return d

    def send_all_block_hash_trees(self):
        self.log("sending block hash trees", level=log.NOISY)
        self.set_status("Sending Subshare Hash Trees")
        self.set_encode_and_push_progress(extra=0.4)
        dl = []
        for shareid,hashes in enumerate(self.block_hashes):
            # hashes is a list of the hashes of all blocks that were sent
            # to shareholder[shareid].
            dl.append(self.send_one_block_hash_tree(shareid, hashes))
        return self._gather_responses(dl)

    def send_one_block_hash_tree(self, shareid, block_hashes):
        t = HashTree(block_hashes)
        all_hashes = list(t)
        # all_hashes[0] is the root hash, == hash(ah[1]+ah[2])
        # all_hashes[1] is the left child, == hash(ah[3]+ah[4])
        # all_hashes[n] == hash(all_hashes[2*n+1] + all_hashes[2*n+2])
        self.share_root_hashes[shareid] = t[0]
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.put_block_hashes(all_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_block_hashes")
        return d

    def send_all_share_hash_trees(self):
        # Each bucket gets a set of share hash tree nodes that are needed to validate their
        # share. This includes the share hash itself, but does not include the top-level hash
        # root (which is stored securely in the URI instead).
        self.log("sending all share hash trees", level=log.NOISY)
        self.set_status("Sending Share Hash Trees")
        self.set_encode_and_push_progress(extra=0.6)
        dl = []
        for h in self.share_root_hashes:
            assert h
        # create the share hash tree
        t = HashTree(self.share_root_hashes)
        # the root of this hash tree goes into our URI
        self.uri_extension_data['share_root_hash'] = t[0]
        # now send just the necessary pieces out to each shareholder
        for i in range(self.num_shares):
            # the HashTree is given a list of leaves: 0,1,2,3..n .
            # These become nodes A+0,A+1,A+2.. of the tree, where A=n-1
            needed_hash_indices = t.needed_hashes(i, include_leaf=True)
            hashes = [(hi, t[hi]) for hi in needed_hash_indices]
            dl.append(self.send_one_share_hash_tree(i, hashes))
        return self._gather_responses(dl)

    def send_one_share_hash_tree(self, shareid, needed_hashes):
        if shareid not in self.landlords:
            return defer.succeed(None)
        sh = self.landlords[shareid]
        d = sh.put_share_hashes(needed_hashes)
        d.addErrback(self._remove_shareholder, shareid, "put_share_hashes")
        return d

    def send_uri_extension_to_all_shareholders(self):
        lp = self.log("sending uri_extension", level=log.NOISY)
        self.set_status("Sending URI Extensions")
        self.set_encode_and_push_progress(extra=0.8)
        for k in ('crypttext_root_hash', 'crypttext_hash',
                  ):
            assert k in self.uri_extension_data
        uri_extension = uri.pack_extension(self.uri_extension_data)
        ed = {}
        for k,v in self.uri_extension_data.items():
            if k.endswith("hash"):
                ed[k] = base32.b2a(v)
            else:
                ed[k] = v
        self.log("uri_extension_data is %s" % (ed,), level=log.NOISY, parent=lp)
        self.uri_extension_hash = hashutil.uri_extension_hash(uri_extension)
        dl = []
        for shareid in list(self.landlords):
            dl.append(self.send_uri_extension(shareid, uri_extension))
        return self._gather_responses(dl)

    def send_uri_extension(self, shareid, uri_extension):
        sh = self.landlords[shareid]
        d = sh.put_uri_extension(uri_extension)
        d.addErrback(self._remove_shareholder, shareid, "put_uri_extension")
        return d

    def close_all_shareholders(self):
        self.log("closing shareholders", level=log.NOISY)
        self.set_status("Closing Shareholders")
        self.set_encode_and_push_progress(extra=0.9)
        dl = []
        for shareid in list(self.landlords):
            d = self.landlords[shareid].close()
            d.addErrback(self._remove_shareholder, shareid, "close")
            dl.append(d)
        return self._gather_responses(dl)

    def done(self, res):
        self.log("upload done", level=log.OPERATIONAL)
        self.set_status("Finished")
        self.set_encode_and_push_progress(extra=1.0) # done
        now = time.time()
        h_and_c_elapsed = now - self._start_hashing_and_close_timestamp
        self._times["hashes_and_close"] = h_and_c_elapsed
        total_elapsed = now - self._start_total_timestamp
        self._times["total_encode_and_push"] = total_elapsed

        # update our sharemap
        self._shares_placed = set(self.landlords.keys())
        return uri.CHKFileVerifierURI(self._storage_index, self.uri_extension_hash,
                                      self.required_shares, self.num_shares, self.file_size)

    def err(self, f):
        self.log("upload failed", failure=f, level=log.UNUSUAL)
        self.set_status("Failed")
        # we need to abort any remaining shareholders, so they'll delete the
        # partial share, allowing someone else to upload it again.
        self.log("aborting shareholders", level=log.UNUSUAL)
        for shareid in list(self.landlords):
            self.landlords[shareid].abort()
        if f.check(defer.FirstError):
            return f.value.subFailure
        return f

    def get_shares_placed(self):
        # return a set of share numbers that were successfully placed.
        return self._shares_placed

    def get_times(self):
        # return a dictionary of encode+push timings
        return self._times

    def get_uri_extension_data(self):
        return self.uri_extension_data
    def get_uri_extension_hash(self):
        return self.uri_extension_hash
