from __future__ import print_function

# do not import any allmydata modules at this level. Do that from inside
# individual functions instead.
import struct, time, os, sys
from twisted.python import usage, failure
from twisted.internet import defer
from foolscap.logging import cli as foolscap_cli
from allmydata.scripts.common import BaseOptions


class DumpOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-options] debug dump-share SHARE_FILENAME"

    optFlags = [
        ["offsets", None, "Display a table of section offsets."],
        ["leases-only", None, "Dump leases but not CHK contents."],
        ]

    description = """
Print lots of information about the given share, by parsing the share's
contents. This includes share type, lease information, encoding parameters,
hash-tree roots, public keys, and segment sizes. This command also emits a
verify-cap for the file that uses the share.

 tahoe debug dump-share testgrid/node-3/storage/shares/4v/4vozh77tsrw7mdhnj7qvp5ky74/0
"""

    def parseArgs(self, filename):
        from allmydata.util.encodingutil import argv_to_abspath
        self['filename'] = argv_to_abspath(filename)

def dump_share(options):
    from allmydata.storage.mutable import MutableShareFile
    from allmydata.util.encodingutil import quote_output

    out = options.stdout

    # check the version, to see if we have a mutable or immutable share
    print("share filename: %s" % quote_output(options['filename']), file=out)

    f = open(options['filename'], "rb")
    prefix = f.read(32)
    f.close()
    if prefix == MutableShareFile.MAGIC:
        return dump_mutable_share(options)
    # otherwise assume it's immutable
    return dump_immutable_share(options)

def dump_immutable_share(options):
    from allmydata.storage.immutable import ShareFile

    out = options.stdout
    f = ShareFile(options['filename'])
    if not options["leases-only"]:
        dump_immutable_chk_share(f, out, options)
    dump_immutable_lease_info(f, out)
    print(file=out)
    return 0

def dump_immutable_chk_share(f, out, options):
    from allmydata import uri
    from allmydata.util import base32
    from allmydata.immutable.layout import ReadBucketProxy
    from allmydata.util.encodingutil import quote_output, to_str

    # use a ReadBucketProxy to parse the bucket and find the uri extension
    bp = ReadBucketProxy(None, None, '')
    offsets = bp._parse_offsets(f.read_share_data(0, 0x44))
    print("%20s: %d" % ("version", bp._version), file=out)
    seek = offsets['uri_extension']
    length = struct.unpack(bp._fieldstruct,
                           f.read_share_data(seek, bp._fieldsize))[0]
    seek += bp._fieldsize
    UEB_data = f.read_share_data(seek, length)

    unpacked = uri.unpack_extension_readable(UEB_data)
    keys1 = ("size", "num_segments", "segment_size",
             "needed_shares", "total_shares")
    keys2 = ("codec_name", "codec_params", "tail_codec_params")
    keys3 = ("plaintext_hash", "plaintext_root_hash",
             "crypttext_hash", "crypttext_root_hash",
             "share_root_hash", "UEB_hash")
    display_keys = {"size": "file_size"}
    for k in keys1:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print("%20s: %s" % (dk, unpacked[k]), file=out)
    print(file=out)
    for k in keys2:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print("%20s: %s" % (dk, unpacked[k]), file=out)
    print(file=out)
    for k in keys3:
        if k in unpacked:
            dk = display_keys.get(k, k)
            print("%20s: %s" % (dk, unpacked[k]), file=out)

    leftover = set(unpacked.keys()) - set(keys1 + keys2 + keys3)
    if leftover:
        print(file=out)
        print("LEFTOVER:", file=out)
        for k in sorted(leftover):
            print("%20s: %s" % (k, unpacked[k]), file=out)

    # the storage index isn't stored in the share itself, so we depend upon
    # knowing the parent directory name to get it
    pieces = options['filename'].split(os.sep)
    if len(pieces) >= 2:
        piece = to_str(pieces[-2])
        if base32.could_be_base32_encoded(piece):
            storage_index = base32.a2b(piece)
            uri_extension_hash = base32.a2b(unpacked["UEB_hash"])
            u = uri.CHKFileVerifierURI(storage_index, uri_extension_hash,
                                      unpacked["needed_shares"],
                                      unpacked["total_shares"], unpacked["size"])
            verify_cap = u.to_string()
            print("%20s: %s" % ("verify-cap", quote_output(verify_cap, quotemarks=False)), file=out)

    sizes = {}
    sizes['data'] = (offsets['plaintext_hash_tree'] -
                           offsets['data'])
    sizes['validation'] = (offsets['uri_extension'] -
                           offsets['plaintext_hash_tree'])
    sizes['uri-extension'] = len(UEB_data)
    print(file=out)
    print(" Size of data within the share:", file=out)
    for k in sorted(sizes):
        print("%20s: %s" % (k, sizes[k]), file=out)

    if options['offsets']:
        print(file=out)
        print(" Section Offsets:", file=out)
        print("%20s: %s" % ("share data", f._data_offset), file=out)
        for k in ["data", "plaintext_hash_tree", "crypttext_hash_tree",
                  "block_hashes", "share_hashes", "uri_extension"]:
            name = {"data": "block data"}.get(k,k)
            offset = f._data_offset + offsets[k]
            print("  %20s: %s   (0x%x)" % (name, offset, offset), file=out)
        print("%20s: %s" % ("leases", f._lease_offset), file=out)

def dump_immutable_lease_info(f, out):
    # display lease information too
    print(file=out)
    leases = list(f.get_leases())
    if leases:
        for i,lease in enumerate(leases):
            when = format_expiration_time(lease.expiration_time)
            print(" Lease #%d: owner=%d, expire in %s" \
                  % (i, lease.owner_num, when), file=out)
    else:
        print(" No leases.", file=out)

def format_expiration_time(expiration_time):
    now = time.time()
    remains = expiration_time - now
    when = "%ds" % remains
    if remains > 24*3600:
        when += " (%d days)" % (remains / (24*3600))
    elif remains > 3600:
        when += " (%d hours)" % (remains / 3600)
    return when


def dump_mutable_share(options):
    from allmydata.storage.mutable import MutableShareFile
    from allmydata.util import base32, idlib
    out = options.stdout
    m = MutableShareFile(options['filename'])
    f = open(options['filename'], "rb")
    WE, nodeid = m._read_write_enabler_and_nodeid(f)
    num_extra_leases = m._read_num_extra_leases(f)
    data_length = m._read_data_length(f)
    extra_lease_offset = m._read_extra_lease_offset(f)
    container_size = extra_lease_offset - m.DATA_OFFSET
    leases = list(m._enumerate_leases(f))

    share_type = "unknown"
    f.seek(m.DATA_OFFSET)
    version = f.read(1)
    if version == "\x00":
        # this slot contains an SMDF share
        share_type = "SDMF"
    elif version == "\x01":
        share_type = "MDMF"
    f.close()

    print(file=out)
    print("Mutable slot found:", file=out)
    print(" share_type: %s" % share_type, file=out)
    print(" write_enabler: %s" % base32.b2a(WE), file=out)
    print(" WE for nodeid: %s" % idlib.nodeid_b2a(nodeid), file=out)
    print(" num_extra_leases: %d" % num_extra_leases, file=out)
    print(" container_size: %d" % container_size, file=out)
    print(" data_length: %d" % data_length, file=out)
    if leases:
        for (leasenum, lease) in leases:
            print(file=out)
            print(" Lease #%d:" % leasenum, file=out)
            print("  ownerid: %d" % lease.owner_num, file=out)
            when = format_expiration_time(lease.expiration_time)
            print("  expires in %s" % when, file=out)
            print("  renew_secret: %s" % base32.b2a(lease.renew_secret), file=out)
            print("  cancel_secret: %s" % base32.b2a(lease.cancel_secret), file=out)
            print("  secrets are for nodeid: %s" % idlib.nodeid_b2a(lease.nodeid), file=out)
    else:
        print("No leases.", file=out)
    print(file=out)

    if share_type == "SDMF":
        dump_SDMF_share(m, data_length, options)
    elif share_type == "MDMF":
        dump_MDMF_share(m, data_length, options)

    return 0

def dump_SDMF_share(m, length, options):
    from allmydata.mutable.layout import unpack_share, unpack_header
    from allmydata.mutable.common import NeedMoreDataError
    from allmydata.util import base32, hashutil
    from allmydata.uri import SSKVerifierURI
    from allmydata.util.encodingutil import quote_output, to_str

    offset = m.DATA_OFFSET

    out = options.stdout

    f = open(options['filename'], "rb")
    f.seek(offset)
    data = f.read(min(length, 2000))
    f.close()

    try:
        pieces = unpack_share(data)
    except NeedMoreDataError as e:
        # retry once with the larger size
        size = e.needed_bytes
        f = open(options['filename'], "rb")
        f.seek(offset)
        data = f.read(min(length, size))
        f.close()
        pieces = unpack_share(data)

    (seqnum, root_hash, IV, k, N, segsize, datalen,
     pubkey, signature, share_hash_chain, block_hash_tree,
     share_data, enc_privkey) = pieces
    (ig_version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
     ig_datalen, offsets) = unpack_header(data)

    print(" SDMF contents:", file=out)
    print("  seqnum: %d" % seqnum, file=out)
    print("  root_hash: %s" % base32.b2a(root_hash), file=out)
    print("  IV: %s" % base32.b2a(IV), file=out)
    print("  required_shares: %d" % k, file=out)
    print("  total_shares: %d" % N, file=out)
    print("  segsize: %d" % segsize, file=out)
    print("  datalen: %d" % datalen, file=out)
    print("  enc_privkey: %d bytes" % len(enc_privkey), file=out)
    print("  pubkey: %d bytes" % len(pubkey), file=out)
    print("  signature: %d bytes" % len(signature), file=out)
    share_hash_ids = ",".join(sorted([str(hid)
                                      for hid in share_hash_chain.keys()]))
    print("  share_hash_chain: %s" % share_hash_ids, file=out)
    print("  block_hash_tree: %d nodes" % len(block_hash_tree), file=out)

    # the storage index isn't stored in the share itself, so we depend upon
    # knowing the parent directory name to get it
    pieces = options['filename'].split(os.sep)
    if len(pieces) >= 2:
        piece = to_str(pieces[-2])
        if base32.could_be_base32_encoded(piece):
            storage_index = base32.a2b(piece)
            fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey)
            u = SSKVerifierURI(storage_index, fingerprint)
            verify_cap = u.to_string()
            print("  verify-cap:", quote_output(verify_cap, quotemarks=False), file=out)

    if options['offsets']:
        # NOTE: this offset-calculation code is fragile, and needs to be
        # merged with MutableShareFile's internals.
        print(file=out)
        print(" Section Offsets:", file=out)
        def printoffset(name, value, shift=0):
            print("%s%20s: %s   (0x%x)" % (" "*shift, name, value, value), file=out)
        printoffset("first lease", m.HEADER_SIZE)
        printoffset("share data", m.DATA_OFFSET)
        o_seqnum = m.DATA_OFFSET + struct.calcsize(">B")
        printoffset("seqnum", o_seqnum, 2)
        o_root_hash = m.DATA_OFFSET + struct.calcsize(">BQ")
        printoffset("root_hash", o_root_hash, 2)
        for k in ["signature", "share_hash_chain", "block_hash_tree",
                  "share_data",
                  "enc_privkey", "EOF"]:
            name = {"share_data": "block data",
                    "EOF": "end of share data"}.get(k,k)
            offset = m.DATA_OFFSET + offsets[k]
            printoffset(name, offset, 2)
        f = open(options['filename'], "rb")
        printoffset("extra leases", m._read_extra_lease_offset(f) + 4)
        f.close()

    print(file=out)

def dump_MDMF_share(m, length, options):
    from allmydata.mutable.layout import MDMFSlotReadProxy
    from allmydata.util import base32, hashutil
    from allmydata.uri import MDMFVerifierURI
    from allmydata.util.encodingutil import quote_output, to_str

    offset = m.DATA_OFFSET
    out = options.stdout

    f = open(options['filename'], "rb")
    storage_index = None; shnum = 0

    class ShareDumper(MDMFSlotReadProxy):
        def _read(self, readvs, force_remote=False, queue=False):
            data = []
            for (where,length) in readvs:
                f.seek(offset+where)
                data.append(f.read(length))
            return defer.succeed({shnum: data})

    p = ShareDumper(None, storage_index, shnum)
    def extract(func):
        stash = []
        # these methods return Deferreds, but we happen to know that they run
        # synchronously when not actually talking to a remote server
        d = func()
        d.addCallback(stash.append)
        return stash[0]

    verinfo = extract(p.get_verinfo)
    encprivkey = extract(p.get_encprivkey)
    signature = extract(p.get_signature)
    pubkey = extract(p.get_verification_key)
    block_hash_tree = extract(p.get_blockhashes)
    share_hash_chain = extract(p.get_sharehashes)
    f.close()

    (seqnum, root_hash, salt_to_use, segsize, datalen, k, N, prefix,
     offsets) = verinfo

    print(" MDMF contents:", file=out)
    print("  seqnum: %d" % seqnum, file=out)
    print("  root_hash: %s" % base32.b2a(root_hash), file=out)
    #print >>out, "  IV: %s" % base32.b2a(IV)
    print("  required_shares: %d" % k, file=out)
    print("  total_shares: %d" % N, file=out)
    print("  segsize: %d" % segsize, file=out)
    print("  datalen: %d" % datalen, file=out)
    print("  enc_privkey: %d bytes" % len(encprivkey), file=out)
    print("  pubkey: %d bytes" % len(pubkey), file=out)
    print("  signature: %d bytes" % len(signature), file=out)
    share_hash_ids = ",".join([str(hid)
                               for hid in sorted(share_hash_chain.keys())])
    print("  share_hash_chain: %s" % share_hash_ids, file=out)
    print("  block_hash_tree: %d nodes" % len(block_hash_tree), file=out)

    # the storage index isn't stored in the share itself, so we depend upon
    # knowing the parent directory name to get it
    pieces = options['filename'].split(os.sep)
    if len(pieces) >= 2:
        piece = to_str(pieces[-2])
        if base32.could_be_base32_encoded(piece):
            storage_index = base32.a2b(piece)
            fingerprint = hashutil.ssk_pubkey_fingerprint_hash(pubkey)
            u = MDMFVerifierURI(storage_index, fingerprint)
            verify_cap = u.to_string()
            print("  verify-cap:", quote_output(verify_cap, quotemarks=False), file=out)

    if options['offsets']:
        # NOTE: this offset-calculation code is fragile, and needs to be
        # merged with MutableShareFile's internals.

        print(file=out)
        print(" Section Offsets:", file=out)
        def printoffset(name, value, shift=0):
            print("%s%.20s: %s   (0x%x)" % (" "*shift, name, value, value), file=out)
        printoffset("first lease", m.HEADER_SIZE, 2)
        printoffset("share data", m.DATA_OFFSET, 2)
        o_seqnum = m.DATA_OFFSET + struct.calcsize(">B")
        printoffset("seqnum", o_seqnum, 4)
        o_root_hash = m.DATA_OFFSET + struct.calcsize(">BQ")
        printoffset("root_hash", o_root_hash, 4)
        for k in ["enc_privkey", "share_hash_chain", "signature",
                  "verification_key", "verification_key_end",
                  "share_data", "block_hash_tree", "EOF"]:
            name = {"share_data": "block data",
                    "verification_key": "pubkey",
                    "verification_key_end": "end of pubkey",
                    "EOF": "end of share data"}.get(k,k)
            offset = m.DATA_OFFSET + offsets[k]
            printoffset(name, offset, 4)
        f = open(options['filename'], "rb")
        printoffset("extra leases", m._read_extra_lease_offset(f) + 4, 2)
        f.close()

    print(file=out)



class DumpCapOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-options] debug dump-cap [options] FILECAP"
    optParameters = [
        ["nodeid", "n",
         None, "Specify the storage server nodeid (ASCII), to construct WE and secrets."],
        ["client-secret", "c", None,
         "Specify the client's base secret (ASCII), to construct secrets."],
        ["client-dir", "d", None,
         "Specify the client's base directory, from which a -c secret will be read."],
        ]
    def parseArgs(self, cap):
        self.cap = cap

    description = """
Print information about the given cap-string (aka: URI, file-cap, dir-cap,
read-cap, write-cap). The URI string is parsed and unpacked. This prints the
type of the cap, its storage index, and any derived keys.

 tahoe debug dump-cap URI:SSK-Verifier:4vozh77tsrw7mdhnj7qvp5ky74:q7f3dwz76sjys4kqfdt3ocur2pay3a6rftnkqmi2uxu3vqsdsofq

This may be useful to determine if a read-cap and a write-cap refer to the
same time, or to extract the storage-index from a file-cap (to then use with
find-shares)

If additional information is provided (storage server nodeid and/or client
base secret), this command will compute the shared secrets used for the
write-enabler and for lease-renewal.
"""


def dump_cap(options):
    from allmydata import uri
    from allmydata.util import base32
    from base64 import b32decode
    import urlparse, urllib

    out = options.stdout
    cap = options.cap
    nodeid = None
    if options['nodeid']:
        nodeid = b32decode(options['nodeid'].upper())
    secret = None
    if options['client-secret']:
        secret = base32.a2b(options['client-secret'])
    elif options['client-dir']:
        secretfile = os.path.join(options['client-dir'], "private", "secret")
        try:
            secret = base32.a2b(open(secretfile, "r").read().strip())
        except EnvironmentError:
            pass

    if cap.startswith("http"):
        scheme, netloc, path, params, query, fragment = urlparse.urlparse(cap)
        assert path.startswith("/uri/")
        cap = urllib.unquote(path[len("/uri/"):])

    u = uri.from_string(cap)

    print(file=out)
    dump_uri_instance(u, nodeid, secret, out)

def _dump_secrets(storage_index, secret, nodeid, out):
    from allmydata.util import hashutil
    from allmydata.util import base32

    if secret:
        crs = hashutil.my_renewal_secret_hash(secret)
        print(" client renewal secret:", base32.b2a(crs), file=out)
        frs = hashutil.file_renewal_secret_hash(crs, storage_index)
        print(" file renewal secret:", base32.b2a(frs), file=out)
        if nodeid:
            renew = hashutil.bucket_renewal_secret_hash(frs, nodeid)
            print(" lease renewal secret:", base32.b2a(renew), file=out)
        ccs = hashutil.my_cancel_secret_hash(secret)
        print(" client cancel secret:", base32.b2a(ccs), file=out)
        fcs = hashutil.file_cancel_secret_hash(ccs, storage_index)
        print(" file cancel secret:", base32.b2a(fcs), file=out)
        if nodeid:
            cancel = hashutil.bucket_cancel_secret_hash(fcs, nodeid)
            print(" lease cancel secret:", base32.b2a(cancel), file=out)

def dump_uri_instance(u, nodeid, secret, out, show_header=True):
    from allmydata import uri
    from allmydata.storage.server import si_b2a
    from allmydata.util import base32, hashutil
    from allmydata.util.encodingutil import quote_output

    if isinstance(u, uri.CHKFileURI):
        if show_header:
            print("CHK File:", file=out)
        print(" key:", base32.b2a(u.key), file=out)
        print(" UEB hash:", base32.b2a(u.uri_extension_hash), file=out)
        print(" size:", u.size, file=out)
        print(" k/N: %d/%d" % (u.needed_shares, u.total_shares), file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)
        _dump_secrets(u.get_storage_index(), secret, nodeid, out)
    elif isinstance(u, uri.CHKFileVerifierURI):
        if show_header:
            print("CHK Verifier URI:", file=out)
        print(" UEB hash:", base32.b2a(u.uri_extension_hash), file=out)
        print(" size:", u.size, file=out)
        print(" k/N: %d/%d" % (u.needed_shares, u.total_shares), file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)

    elif isinstance(u, uri.LiteralFileURI):
        if show_header:
            print("Literal File URI:", file=out)
        print(" data:", quote_output(u.data), file=out)

    elif isinstance(u, uri.WriteableSSKFileURI): # SDMF
        if show_header:
            print("SDMF Writeable URI:", file=out)
        print(" writekey:", base32.b2a(u.writekey), file=out)
        print(" readkey:", base32.b2a(u.readkey), file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)
        print(" fingerprint:", base32.b2a(u.fingerprint), file=out)
        print(file=out)
        if nodeid:
            we = hashutil.ssk_write_enabler_hash(u.writekey, nodeid)
            print(" write_enabler:", base32.b2a(we), file=out)
            print(file=out)
        _dump_secrets(u.get_storage_index(), secret, nodeid, out)
    elif isinstance(u, uri.ReadonlySSKFileURI):
        if show_header:
            print("SDMF Read-only URI:", file=out)
        print(" readkey:", base32.b2a(u.readkey), file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)
        print(" fingerprint:", base32.b2a(u.fingerprint), file=out)
    elif isinstance(u, uri.SSKVerifierURI):
        if show_header:
            print("SDMF Verifier URI:", file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)
        print(" fingerprint:", base32.b2a(u.fingerprint), file=out)

    elif isinstance(u, uri.WriteableMDMFFileURI): # MDMF
        if show_header:
            print("MDMF Writeable URI:", file=out)
        print(" writekey:", base32.b2a(u.writekey), file=out)
        print(" readkey:", base32.b2a(u.readkey), file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)
        print(" fingerprint:", base32.b2a(u.fingerprint), file=out)
        print(file=out)
        if nodeid:
            we = hashutil.ssk_write_enabler_hash(u.writekey, nodeid)
            print(" write_enabler:", base32.b2a(we), file=out)
            print(file=out)
        _dump_secrets(u.get_storage_index(), secret, nodeid, out)
    elif isinstance(u, uri.ReadonlyMDMFFileURI):
        if show_header:
            print("MDMF Read-only URI:", file=out)
        print(" readkey:", base32.b2a(u.readkey), file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)
        print(" fingerprint:", base32.b2a(u.fingerprint), file=out)
    elif isinstance(u, uri.MDMFVerifierURI):
        if show_header:
            print("MDMF Verifier URI:", file=out)
        print(" storage index:", si_b2a(u.get_storage_index()), file=out)
        print(" fingerprint:", base32.b2a(u.fingerprint), file=out)


    elif isinstance(u, uri.ImmutableDirectoryURI): # CHK-based directory
        if show_header:
            print("CHK Directory URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    elif isinstance(u, uri.ImmutableDirectoryURIVerifier):
        if show_header:
            print("CHK Directory Verifier URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)

    elif isinstance(u, uri.DirectoryURI): # SDMF-based directory
        if show_header:
            print("Directory Writeable URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    elif isinstance(u, uri.ReadonlyDirectoryURI):
        if show_header:
            print("Directory Read-only URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    elif isinstance(u, uri.DirectoryURIVerifier):
        if show_header:
            print("Directory Verifier URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)

    elif isinstance(u, uri.MDMFDirectoryURI): # MDMF-based directory
        if show_header:
            print("Directory Writeable URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    elif isinstance(u, uri.ReadonlyMDMFDirectoryURI):
        if show_header:
            print("Directory Read-only URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)
    elif isinstance(u, uri.MDMFDirectoryURIVerifier):
        if show_header:
            print("Directory Verifier URI:", file=out)
        dump_uri_instance(u._filenode_uri, nodeid, secret, out, False)

    else:
        print("unknown cap type", file=out)

class FindSharesOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-options] debug find-shares STORAGE_INDEX NODEDIRS.."

    def parseArgs(self, storage_index_s, *nodedirs):
        from allmydata.util.encodingutil import argv_to_abspath
        self.si_s = storage_index_s
        self.nodedirs = map(argv_to_abspath, nodedirs)

    description = """
Locate all shares for the given storage index. This command looks through one
or more node directories to find the shares. It returns a list of filenames,
one per line, for each share file found.

 tahoe debug find-shares 4vozh77tsrw7mdhnj7qvp5ky74 testgrid/node-*

It may be useful during testing, when running a test grid in which all the
nodes are on a local disk. The share files thus located can be counted,
examined (with dump-share), or corrupted/deleted to test checker/repairer.
"""

def find_shares(options):
    """Given a storage index and a list of node directories, emit a list of
    all matching shares to stdout, one per line. For example:

     find-shares.py 44kai1tui348689nrw8fjegc8c ~/testnet/node-*

    gives:

    /home/warner/testnet/node-1/storage/shares/44k/44kai1tui348689nrw8fjegc8c/5
    /home/warner/testnet/node-1/storage/shares/44k/44kai1tui348689nrw8fjegc8c/9
    /home/warner/testnet/node-2/storage/shares/44k/44kai1tui348689nrw8fjegc8c/2
    """
    from allmydata.storage.server import si_a2b, storage_index_to_dir
    from allmydata.util.encodingutil import listdir_unicode, quote_local_unicode_path

    out = options.stdout
    sharedir = storage_index_to_dir(si_a2b(options.si_s))
    for d in options.nodedirs:
        d = os.path.join(d, "storage", "shares", sharedir)
        if os.path.exists(d):
            for shnum in listdir_unicode(d):
                print(quote_local_unicode_path(os.path.join(d, shnum), quotemarks=False), file=out)

    return 0


class CatalogSharesOptions(BaseOptions):
    def parseArgs(self, *nodedirs):
        from allmydata.util.encodingutil import argv_to_abspath
        self.nodedirs = map(argv_to_abspath, nodedirs)
        if not nodedirs:
            raise usage.UsageError("must specify at least one node directory")

    def getSynopsis(self):
        return "Usage: tahoe [global-options] debug catalog-shares NODEDIRS.."

    description = """
Locate all shares in the given node directories, and emit a one-line summary
of each share. Run it like this:

 tahoe debug catalog-shares testgrid/node-* >allshares.txt

The lines it emits will look like the following:

 CHK $SI $k/$N $filesize $UEB_hash $expiration $abspath_sharefile
 SDMF $SI $k/$N $filesize $seqnum/$roothash $expiration $abspath_sharefile
 UNKNOWN $abspath_sharefile

This command can be used to build up a catalog of shares from many storage
servers and then sort the results to compare all shares for the same file. If
you see shares with the same SI but different parameters/filesize/UEB_hash,
then something is wrong. The misc/find-share/anomalies.py script may be
useful for purpose.
"""

def call(c, *args, **kwargs):
    # take advantage of the fact that ImmediateReadBucketProxy returns
    # Deferreds that are already fired
    results = []
    failures = []
    d = defer.maybeDeferred(c, *args, **kwargs)
    d.addCallbacks(results.append, failures.append)
    if failures:
        failures[0].raiseException()
    return results[0]

def describe_share(abs_sharefile, si_s, shnum_s, now, out):
    from allmydata import uri
    from allmydata.storage.mutable import MutableShareFile
    from allmydata.storage.immutable import ShareFile
    from allmydata.mutable.layout import unpack_share
    from allmydata.mutable.common import NeedMoreDataError
    from allmydata.immutable.layout import ReadBucketProxy
    from allmydata.util import base32
    from allmydata.util.encodingutil import quote_output
    import struct

    f = open(abs_sharefile, "rb")
    prefix = f.read(32)

    if prefix == MutableShareFile.MAGIC:
        # mutable share
        m = MutableShareFile(abs_sharefile)
        WE, nodeid = m._read_write_enabler_and_nodeid(f)
        data_length = m._read_data_length(f)
        expiration_time = min( [lease.expiration_time
                                for (i,lease) in m._enumerate_leases(f)] )
        expiration = max(0, expiration_time - now)

        share_type = "unknown"
        f.seek(m.DATA_OFFSET)
        version = f.read(1)
        if version == "\x00":
            # this slot contains an SMDF share
            share_type = "SDMF"
        elif version == "\x01":
            share_type = "MDMF"

        if share_type == "SDMF":
            f.seek(m.DATA_OFFSET)
            data = f.read(min(data_length, 2000))

            try:
                pieces = unpack_share(data)
            except NeedMoreDataError as e:
                # retry once with the larger size
                size = e.needed_bytes
                f.seek(m.DATA_OFFSET)
                data = f.read(min(data_length, size))
                pieces = unpack_share(data)
            (seqnum, root_hash, IV, k, N, segsize, datalen,
             pubkey, signature, share_hash_chain, block_hash_tree,
             share_data, enc_privkey) = pieces

            print("SDMF %s %d/%d %d #%d:%s %d %s" % \
                  (si_s, k, N, datalen,
                   seqnum, base32.b2a(root_hash),
                   expiration, quote_output(abs_sharefile)), file=out)
        elif share_type == "MDMF":
            from allmydata.mutable.layout import MDMFSlotReadProxy
            fake_shnum = 0
            # TODO: factor this out with dump_MDMF_share()
            class ShareDumper(MDMFSlotReadProxy):
                def _read(self, readvs, force_remote=False, queue=False):
                    data = []
                    for (where,length) in readvs:
                        f.seek(m.DATA_OFFSET+where)
                        data.append(f.read(length))
                    return defer.succeed({fake_shnum: data})

            p = ShareDumper(None, "fake-si", fake_shnum)
            def extract(func):
                stash = []
                # these methods return Deferreds, but we happen to know that
                # they run synchronously when not actually talking to a
                # remote server
                d = func()
                d.addCallback(stash.append)
                return stash[0]

            verinfo = extract(p.get_verinfo)
            (seqnum, root_hash, salt_to_use, segsize, datalen, k, N, prefix,
             offsets) = verinfo
            print("MDMF %s %d/%d %d #%d:%s %d %s" % \
                  (si_s, k, N, datalen,
                   seqnum, base32.b2a(root_hash),
                   expiration, quote_output(abs_sharefile)), file=out)
        else:
            print("UNKNOWN mutable %s" % quote_output(abs_sharefile), file=out)

    elif struct.unpack(">L", prefix[:4]) == (1,):
        # immutable

        class ImmediateReadBucketProxy(ReadBucketProxy):
            def __init__(self, sf):
                self.sf = sf
                ReadBucketProxy.__init__(self, None, None, "")
            def __repr__(self):
                return "<ImmediateReadBucketProxy>"
            def _read(self, offset, size):
                return defer.succeed(sf.read_share_data(offset, size))

        # use a ReadBucketProxy to parse the bucket and find the uri extension
        sf = ShareFile(abs_sharefile)
        bp = ImmediateReadBucketProxy(sf)

        expiration_time = min( [lease.expiration_time
                                for lease in sf.get_leases()] )
        expiration = max(0, expiration_time - now)

        UEB_data = call(bp.get_uri_extension)
        unpacked = uri.unpack_extension_readable(UEB_data)

        k = unpacked["needed_shares"]
        N = unpacked["total_shares"]
        filesize = unpacked["size"]
        ueb_hash = unpacked["UEB_hash"]

        print("CHK %s %d/%d %d %s %d %s" % (si_s, k, N, filesize,
                                                   ueb_hash, expiration,
                                                   quote_output(abs_sharefile)), file=out)

    else:
        print("UNKNOWN really-unknown %s" % quote_output(abs_sharefile), file=out)

    f.close()

def catalog_shares(options):
    from allmydata.util.encodingutil import listdir_unicode, quote_output

    out = options.stdout
    err = options.stderr
    now = time.time()
    for d in options.nodedirs:
        d = os.path.join(d, "storage", "shares")
        try:
            abbrevs = listdir_unicode(d)
        except EnvironmentError:
            # ignore nodes that have storage turned off altogether
            pass
        else:
            for abbrevdir in sorted(abbrevs):
                if abbrevdir == "incoming":
                    continue
                abbrevdir = os.path.join(d, abbrevdir)
                # this tool may get run against bad disks, so we can't assume
                # that listdir_unicode will always succeed. Try to catalog as much
                # as possible.
                try:
                    sharedirs = listdir_unicode(abbrevdir)
                    for si_s in sorted(sharedirs):
                        si_dir = os.path.join(abbrevdir, si_s)
                        catalog_shares_one_abbrevdir(si_s, si_dir, now, out,err)
                except:
                    print("Error processing %s" % quote_output(abbrevdir), file=err)
                    failure.Failure().printTraceback(err)

    return 0

def _as_number(s):
    try:
        return int(s)
    except ValueError:
        return "not int"

def catalog_shares_one_abbrevdir(si_s, si_dir, now, out, err):
    from allmydata.util.encodingutil import listdir_unicode, quote_output

    try:
        for shnum_s in sorted(listdir_unicode(si_dir), key=_as_number):
            abs_sharefile = os.path.join(si_dir, shnum_s)
            assert os.path.isfile(abs_sharefile)
            try:
                describe_share(abs_sharefile, si_s, shnum_s, now,
                               out)
            except:
                print("Error processing %s" % quote_output(abs_sharefile), file=err)
                failure.Failure().printTraceback(err)
    except:
        print("Error processing %s" % quote_output(si_dir), file=err)
        failure.Failure().printTraceback(err)

class CorruptShareOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe [global-options] debug corrupt-share SHARE_FILENAME"

    optParameters = [
        ["offset", "o", "block-random", "Specify which bit to flip."],
        ]

    description = """
Corrupt the given share by flipping a bit. This will cause a
verifying/downloading client to log an integrity-check failure incident, and
downloads will proceed with a different share.

The --offset parameter controls which bit should be flipped. The default is
to flip a single random bit of the block data.

 tahoe debug corrupt-share testgrid/node-3/storage/shares/4v/4vozh77tsrw7mdhnj7qvp5ky74/0

Obviously, this command should not be used in normal operation.
"""
    def parseArgs(self, filename):
        self['filename'] = filename

def corrupt_share(options):
    import random
    from allmydata.storage.mutable import MutableShareFile
    from allmydata.storage.immutable import ShareFile
    from allmydata.mutable.layout import unpack_header
    from allmydata.immutable.layout import ReadBucketProxy
    out = options.stdout
    fn = options['filename']
    assert options["offset"] == "block-random", "other offsets not implemented"
    # first, what kind of share is it?

    def flip_bit(start, end):
        offset = random.randrange(start, end)
        bit = random.randrange(0, 8)
        print("[%d..%d):  %d.b%d" % (start, end, offset, bit), file=out)
        f = open(fn, "rb+")
        f.seek(offset)
        d = f.read(1)
        d = chr(ord(d) ^ 0x01)
        f.seek(offset)
        f.write(d)
        f.close()

    f = open(fn, "rb")
    prefix = f.read(32)
    f.close()
    if prefix == MutableShareFile.MAGIC:
        # mutable
        m = MutableShareFile(fn)
        f = open(fn, "rb")
        f.seek(m.DATA_OFFSET)
        data = f.read(2000)
        # make sure this slot contains an SMDF share
        assert data[0] == "\x00", "non-SDMF mutable shares not supported"
        f.close()

        (version, ig_seqnum, ig_roothash, ig_IV, ig_k, ig_N, ig_segsize,
         ig_datalen, offsets) = unpack_header(data)

        assert version == 0, "we only handle v0 SDMF files"
        start = m.DATA_OFFSET + offsets["share_data"]
        end = m.DATA_OFFSET + offsets["enc_privkey"]
        flip_bit(start, end)
    else:
        # otherwise assume it's immutable
        f = ShareFile(fn)
        bp = ReadBucketProxy(None, None, '')
        offsets = bp._parse_offsets(f.read_share_data(0, 0x24))
        start = f._data_offset + offsets["data"]
        end = f._data_offset + offsets["plaintext_hash_tree"]
        flip_bit(start, end)



class ReplOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe debug repl (OBSOLETE)"

def repl(options):
    print("'tahoe debug repl' is obsolete. Please run 'python' in a virtualenv.", file=options.stderr)
    return 1


DEFAULT_TESTSUITE = 'allmydata'

class TrialOptions(BaseOptions):
    def getSynopsis(self):
        return "Usage: tahoe debug trial (OBSOLETE)"

def trial(config):
    print("'tahoe debug trial' is obsolete. Please run 'tox', or use 'trial' in a virtualenv.", file=config.stderr)
    return 1

def fixOptionsClass(args):
    (subcmd, shortcut, OptionsClass, desc) = args
    class FixedOptionsClass(OptionsClass):
        def getSynopsis(self):
            t = OptionsClass.getSynopsis(self)
            i = t.find("Usage: flogtool ")
            if i >= 0:
                return "Usage: tahoe [global-options] debug flogtool " + t[i+len("Usage: flogtool "):]
            else:
                return "Usage: tahoe [global-options] debug flogtool %s [options]" % (subcmd,)
    return (subcmd, shortcut, FixedOptionsClass, desc)

class FlogtoolOptions(foolscap_cli.Options):
    def __init__(self):
        super(FlogtoolOptions, self).__init__()
        self.subCommands = map(fixOptionsClass, self.subCommands)

    def getSynopsis(self):
        return "Usage: tahoe [global-options] debug flogtool COMMAND [flogtool-options]"

    def parseOptions(self, all_subargs, *a, **kw):
        self.flogtool_args = list(all_subargs)
        return super(FlogtoolOptions, self).parseOptions(self.flogtool_args, *a, **kw)

    def getUsage(self, width=None):
        t = super(FlogtoolOptions, self).getUsage(width)
        t += """
The 'tahoe debug flogtool' command uses the correct imports for this instance
of Tahoe-LAFS.

Please run 'tahoe debug flogtool COMMAND --help' for more details on each
subcommand.
"""
        return t

    def opt_help(self):
        print(str(self))
        sys.exit(0)

def flogtool(config):
    sys.argv = ['flogtool'] + config.flogtool_args
    return foolscap_cli.run_flogtool()


class DebugCommand(BaseOptions):
    subCommands = [
        ["dump-share", None, DumpOptions,
         "Unpack and display the contents of a share (uri_extension and leases)."],
        ["dump-cap", None, DumpCapOptions, "Unpack a read-cap or write-cap."],
        ["find-shares", None, FindSharesOptions, "Locate sharefiles in node dirs."],
        ["catalog-shares", None, CatalogSharesOptions, "Describe all shares in node dirs."],
        ["corrupt-share", None, CorruptShareOptions, "Corrupt a share by flipping a bit."],
        ["repl", None, ReplOptions, "OBSOLETE"],
        ["trial", None, TrialOptions, "OBSOLETE"],
        ["flogtool", None, FlogtoolOptions, "Utilities to access log files."],
        ]
    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a subcommand")
    synopsis = "COMMAND"

    def getUsage(self, width=None):
        t = BaseOptions.getUsage(self, width)
        t += """\

Please run e.g. 'tahoe debug dump-share --help' for more details on each
subcommand.
"""
        return t

subDispatch = {
    "dump-share": dump_share,
    "dump-cap": dump_cap,
    "find-shares": find_shares,
    "catalog-shares": catalog_shares,
    "corrupt-share": corrupt_share,
    "repl": repl,
    "trial": trial,
    "flogtool": flogtool,
    }


def do_debug(options):
    so = options.subOptions
    so.stdout = options.stdout
    so.stderr = options.stderr
    f = subDispatch[options.subCommand]
    return f(so)


subCommands = [
    ["debug", None, DebugCommand, "debug subcommands: use 'tahoe debug' for a list."],
    ]

dispatch = {
    "debug": do_debug,
    }
