"""Directory Node implementation."""
import time, unicodedata

from zope.interface import implementer
from twisted.internet import defer
from foolscap.api import fireEventually
import json

from allmydata.crypto import aes
from allmydata.deep_stats import DeepStats
from allmydata.mutable.common import NotWriteableError
from allmydata.mutable.filenode import MutableFileNode
from allmydata.unknown import UnknownNode, strip_prefix_for_ro
from allmydata.interfaces import IFilesystemNode, IDirectoryNode, IFileNode, \
     ExistingChildError, NoSuchChildError, ICheckable, IDeepCheckable, \
     MustBeDeepImmutableError, CapConstraintError, ChildOfWrongTypeError
from allmydata.check_results import DeepCheckResults, \
     DeepCheckAndRepairResults
from allmydata.monitor import Monitor
from allmydata.util import hashutil, base32, log
from allmydata.util.encodingutil import quote_output
from allmydata.util.assertutil import precondition
from allmydata.util.netstring import netstring, split_netstring
from allmydata.util.consumer import download_to_data
from allmydata.uri import wrap_dirnode_cap
from allmydata.util.dictutil import AuxValueDict

from eliot import (
    ActionType,
    Field,
)
from eliot.twisted import (
    DeferredContext,
)

NAME = Field.for_types(
    u"name",
    [unicode],
    u"The name linking the parent to this node.",
)

METADATA = Field.for_types(
    u"metadata",
    [dict],
    u"Data about a node.",
)

OVERWRITE = Field.for_types(
    u"overwrite",
    [bool],
    u"True to replace an existing file of the same name, "
    u"false to fail with a collision error.",
)

ADD_FILE = ActionType(
    u"dirnode:add-file",
    [NAME, METADATA, OVERWRITE],
    [],
    u"Add a new file as a child of a directory.",
)

def update_metadata(metadata, new_metadata, now):
    """Updates 'metadata' in-place with the information in 'new_metadata'.

    Timestamps are set according to the time 'now'.
    """

    if metadata is None:
        metadata = {}

    old_ctime = None
    if 'ctime' in metadata:
        old_ctime = metadata['ctime']

    if new_metadata is not None:
        # Overwrite all metadata.
        newmd = new_metadata.copy()

        # Except 'tahoe'.
        if 'tahoe' in newmd:
            del newmd['tahoe']
        if 'tahoe' in metadata:
            newmd['tahoe'] = metadata['tahoe']

        metadata = newmd

    # update timestamps
    sysmd = metadata.get('tahoe', {})
    if 'linkcrtime' not in sysmd:
        # In Tahoe < 1.4.0 we used the word 'ctime' to mean what Tahoe >= 1.4.0
        # calls 'linkcrtime'. This field is only used if it was in the old metadata,
        # and 'tahoe:linkcrtime' was not.
        if old_ctime is not None:
            sysmd['linkcrtime'] = old_ctime
        else:
            sysmd['linkcrtime'] = now

    sysmd['linkmotime'] = now
    metadata['tahoe'] = sysmd

    return metadata


# 'x' at the end of a variable name indicates that it holds a Unicode string that may not
# be NFC-normalized.

def normalize(namex):
    return unicodedata.normalize('NFC', namex)

# TODO: {Deleter,MetadataSetter,Adder}.modify all start by unpacking the
# contents and end by repacking them. It might be better to apply them to
# the unpacked contents.

class Deleter(object):
    def __init__(self, node, namex, must_exist=True, must_be_directory=False, must_be_file=False):
        self.node = node
        self.name = normalize(namex)
        self.must_exist = must_exist
        self.must_be_directory = must_be_directory
        self.must_be_file = must_be_file

    def modify(self, old_contents, servermap, first_time):
        children = self.node._unpack_contents(old_contents)
        if self.name not in children:
            if first_time and self.must_exist:
                raise NoSuchChildError(self.name)
            self.old_child = None
            return None
        self.old_child, metadata = children[self.name]

        # Unknown children can be removed regardless of must_be_directory or must_be_file.
        if self.must_be_directory and IFileNode.providedBy(self.old_child):
            raise ChildOfWrongTypeError("delete required a directory, not a file")
        if self.must_be_file and IDirectoryNode.providedBy(self.old_child):
            raise ChildOfWrongTypeError("delete required a file, not a directory")

        del children[self.name]
        new_contents = self.node._pack_contents(children)
        return new_contents


class MetadataSetter(object):
    def __init__(self, node, namex, metadata, create_readonly_node=None):
        self.node = node
        self.name = normalize(namex)
        self.metadata = metadata
        self.create_readonly_node = create_readonly_node

    def modify(self, old_contents, servermap, first_time):
        children = self.node._unpack_contents(old_contents)
        name = self.name
        if name not in children:
            raise NoSuchChildError(name)

        now = time.time()
        child = children[name][0]

        metadata = update_metadata(children[name][1].copy(), self.metadata, now)
        if self.create_readonly_node and metadata.get('no-write', False):
            child = self.create_readonly_node(child, name)

        children[name] = (child, metadata)
        new_contents = self.node._pack_contents(children)
        return new_contents


class Adder(object):
    def __init__(self, node, entries=None, overwrite=True, create_readonly_node=None):
        self.node = node
        if entries is None:
            entries = {}
        precondition(isinstance(entries, dict), entries)
        precondition(overwrite in (True, False, "only-files"), overwrite)
        # keys of 'entries' may not be normalized.
        self.entries = entries
        self.overwrite = overwrite
        self.create_readonly_node = create_readonly_node

    def set_node(self, namex, node, metadata):
        precondition(IFilesystemNode.providedBy(node), node)
        self.entries[namex] = (node, metadata)

    def modify(self, old_contents, servermap, first_time):
        children = self.node._unpack_contents(old_contents)
        now = time.time()
        for (namex, (child, new_metadata)) in self.entries.iteritems():
            name = normalize(namex)
            precondition(IFilesystemNode.providedBy(child), child)

            # Strictly speaking this is redundant because we would raise the
            # error again in _pack_normalized_children.
            child.raise_error()

            metadata = None
            if name in children:
                if not self.overwrite:
                    raise ExistingChildError("child %s already exists" % quote_output(name, encoding='utf-8'))

                if self.overwrite == "only-files" and IDirectoryNode.providedBy(children[name][0]):
                    raise ExistingChildError("child %s already exists as a directory" % quote_output(name, encoding='utf-8'))
                metadata = children[name][1].copy()

            metadata = update_metadata(metadata, new_metadata, now)
            if self.create_readonly_node and metadata.get('no-write', False):
                child = self.create_readonly_node(child, name)

            children[name] = (child, metadata)
        new_contents = self.node._pack_contents(children)
        return new_contents

def _encrypt_rw_uri(writekey, rw_uri):
    precondition(isinstance(rw_uri, str), rw_uri)
    precondition(isinstance(writekey, str), writekey)

    salt = hashutil.mutable_rwcap_salt_hash(rw_uri)
    key = hashutil.mutable_rwcap_key_hash(salt, writekey)
    encryptor = aes.create_encryptor(key)
    crypttext = aes.encrypt_data(encryptor, rw_uri)
    mac = hashutil.hmac(key, salt + crypttext)
    assert len(mac) == 32
    return salt + crypttext + mac
    # The MAC is not checked by readers in Tahoe >= 1.3.0, but we still
    # produce it for the sake of older readers.

def pack_children(childrenx, writekey, deep_immutable=False):
    # initial_children must have metadata (i.e. {} instead of None)
    children = {}
    for (namex, (node, metadata)) in childrenx.iteritems():
        precondition(isinstance(metadata, dict),
                     "directory creation requires metadata to be a dict, not None", metadata)
        children[normalize(namex)] = (node, metadata)

    return _pack_normalized_children(children, writekey=writekey, deep_immutable=deep_immutable)


ZERO_LEN_NETSTR=netstring('')
def _pack_normalized_children(children, writekey, deep_immutable=False):
    """Take a dict that maps:
         children[unicode_nfc_name] = (IFileSystemNode, metadata_dict)
    and pack it into a single string, for use as the contents of the backing
    file. This is the same format as is returned by _unpack_contents. I also
    accept an AuxValueDict, in which case I'll use the auxilliary cached data
    as the pre-packed entry, which is faster than re-packing everything each
    time.

    If writekey is provided then I will superencrypt the child's writecap with
    writekey.

    If deep_immutable is True, I will require that all my children are deeply
    immutable, and will raise a MustBeDeepImmutableError if not.
    """
    precondition((writekey is None) or isinstance(writekey, str), writekey)

    has_aux = isinstance(children, AuxValueDict)
    entries = []
    for name in sorted(children.keys()):
        assert isinstance(name, unicode)
        entry = None
        (child, metadata) = children[name]
        child.raise_error()
        if deep_immutable and not child.is_allowed_in_immutable_directory():
            raise MustBeDeepImmutableError("child %s is not allowed in an immutable directory" %
                                           quote_output(name, encoding='utf-8'), name)
        if has_aux:
            entry = children.get_aux(name)
        if not entry:
            assert IFilesystemNode.providedBy(child), (name,child)
            assert isinstance(metadata, dict)
            rw_uri = child.get_write_uri()
            if rw_uri is None:
                rw_uri = ""
            assert isinstance(rw_uri, str), rw_uri

            # should be prevented by MustBeDeepImmutableError check above
            assert not (rw_uri and deep_immutable)

            ro_uri = child.get_readonly_uri()
            if ro_uri is None:
                ro_uri = ""
            assert isinstance(ro_uri, str), ro_uri
            if writekey is not None:
                writecap = netstring(_encrypt_rw_uri(writekey, rw_uri))
            else:
                writecap = ZERO_LEN_NETSTR
            entry = "".join([netstring(name.encode("utf-8")),
                             netstring(strip_prefix_for_ro(ro_uri, deep_immutable)),
                             writecap,
                             netstring(json.dumps(metadata))])
        entries.append(netstring(entry))
    return "".join(entries)

@implementer(IDirectoryNode, ICheckable, IDeepCheckable)
class DirectoryNode(object):
    filenode_class = MutableFileNode

    def __init__(self, filenode, nodemaker, uploader):
        assert IFileNode.providedBy(filenode), filenode
        assert not IDirectoryNode.providedBy(filenode), filenode
        self._node = filenode
        filenode_cap = filenode.get_cap()
        self._uri = wrap_dirnode_cap(filenode_cap)
        self._nodemaker = nodemaker
        self._uploader = uploader

    def __repr__(self):
        return "<%s %s-%s %s>" % (self.__class__.__name__,
                                  self.is_readonly() and "RO" or "RW",
                                  self.is_mutable() and "MUT" or "IMM",
                                  hasattr(self, '_uri') and self._uri.abbrev())

    def get_size(self):
        """Return the size of our backing mutable file, in bytes, if we've
        fetched it. Otherwise return None. This returns synchronously."""
        return self._node.get_size()

    def get_current_size(self):
        """Calculate the size of our backing mutable file, in bytes. Returns
        a Deferred that fires with the result."""
        return self._node.get_current_size()

    def _read(self):
        if self._node.is_mutable():
            # use the IMutableFileNode API.
            d = self._node.download_best_version()
        else:
            d = download_to_data(self._node)
        d.addCallback(self._unpack_contents)
        return d

    def _decrypt_rwcapdata(self, encwrcap):
        salt = encwrcap[:16]
        crypttext = encwrcap[16:-32]
        key = hashutil.mutable_rwcap_key_hash(salt, self._node.get_writekey())
        encryptor = aes.create_decryptor(key)
        plaintext = aes.decrypt_data(encryptor, crypttext)
        return plaintext

    def _create_and_validate_node(self, rw_uri, ro_uri, name):
        # name is just for error reporting
        node = self._nodemaker.create_from_cap(rw_uri, ro_uri,
                                               deep_immutable=not self.is_mutable(),
                                               name=name)
        node.raise_error()
        return node

    def _create_readonly_node(self, node, name):
        # name is just for error reporting
        if not node.is_unknown() and node.is_readonly():
            return node
        return self._create_and_validate_node(None, node.get_readonly_uri(), name=name)

    def _unpack_contents(self, data):
        # the directory is serialized as a list of netstrings, one per child.
        # Each child is serialized as a list of four netstrings: (name, ro_uri,
        # rwcapdata, metadata), in which the name, ro_uri, metadata are in
        # cleartext. The 'name' is UTF-8 encoded, and should be normalized to NFC.
        # The rwcapdata is formatted as:
        # pack("16ss32s", iv, AES(H(writekey+iv), plaintext_rw_uri), mac)
        assert isinstance(data, str), (repr(data), type(data))
        # an empty directory is serialized as an empty string
        if data == "":
            return AuxValueDict()
        writeable = not self.is_readonly()
        mutable = self.is_mutable()
        children = AuxValueDict()
        position = 0
        while position < len(data):
            entries, position = split_netstring(data, 1, position)
            entry = entries[0]
            (namex_utf8, ro_uri, rwcapdata, metadata_s), subpos = split_netstring(entry, 4)
            if not mutable and len(rwcapdata) > 0:
                raise ValueError("the rwcapdata field of a dirnode in an immutable directory was not empty")

            # A name containing characters that are unassigned in one version of Unicode might
            # not be normalized wrt a later version. See the note in section 'Normalization Stability'
            # at <http://unicode.org/policies/stability_policy.html>.
            # Therefore we normalize names going both in and out of directories.
            name = normalize(namex_utf8.decode("utf-8"))

            rw_uri = ""
            if writeable:
                rw_uri = self._decrypt_rwcapdata(rwcapdata)

            # Since the encryption uses CTR mode, it currently leaks the length of the
            # plaintext rw_uri -- and therefore whether it is present, i.e. whether the
            # dirnode is writeable (ticket #925). By stripping trailing spaces in
            # Tahoe >= 1.6.0, we may make it easier for future versions to plug this leak.
            # ro_uri is treated in the same way for consistency.
            # rw_uri and ro_uri will be either None or a non-empty string.

            rw_uri = rw_uri.rstrip(' ') or None
            ro_uri = ro_uri.rstrip(' ') or None

            try:
                child = self._create_and_validate_node(rw_uri, ro_uri, name)
                if mutable or child.is_allowed_in_immutable_directory():
                    metadata = json.loads(metadata_s)
                    assert isinstance(metadata, dict)
                    children[name] = (child, metadata)
                    children.set_with_aux(name, (child, metadata), auxilliary=entry)
                else:
                    log.msg(format="mutable cap for child %(name)s unpacked from an immutable directory",
                            name=quote_output(name, encoding='utf-8'),
                            facility="tahoe.webish", level=log.UNUSUAL)
            except CapConstraintError as e:
                log.msg(format="unmet constraint on cap for child %(name)s unpacked from a directory:\n"
                               "%(message)s", message=e.args[0], name=quote_output(name, encoding='utf-8'),
                               facility="tahoe.webish", level=log.UNUSUAL)

        return children

    def _pack_contents(self, children):
        # expects children in the same format as _unpack_contents returns
        return _pack_normalized_children(children, self._node.get_writekey())

    def is_readonly(self):
        return self._node.is_readonly()

    def is_mutable(self):
        return self._node.is_mutable()

    def is_unknown(self):
        return False

    def is_allowed_in_immutable_directory(self):
        return not self._node.is_mutable()

    def raise_error(self):
        pass

    def get_uri(self):
        return self._uri.to_string()

    def get_write_uri(self):
        if self.is_readonly():
            return None
        return self._uri.to_string()

    def get_readonly_uri(self):
        return self._uri.get_readonly().to_string()

    def get_cap(self):
        return self._uri

    def get_readcap(self):
        return self._uri.get_readonly()

    def get_verify_cap(self):
        return self._uri.get_verify_cap()

    def get_repair_cap(self):
        if self._node.is_readonly():
            return None # readonly (mutable) dirnodes are not yet repairable
        return self._uri

    def get_storage_index(self):
        return self._uri.get_storage_index()

    def check(self, monitor, verify=False, add_lease=False):
        """Perform a file check. See IChecker.check for details."""
        return self._node.check(monitor, verify, add_lease)
    def check_and_repair(self, monitor, verify=False, add_lease=False):
        return self._node.check_and_repair(monitor, verify, add_lease)

    def list(self):
        """I return a Deferred that fires with a dictionary mapping child
        name to a tuple of (IFilesystemNode, metadata)."""
        return self._read()

    def has_child(self, namex):
        """I return a Deferred that fires with a boolean, True if there
        exists a child of the given name, False if not."""
        name = normalize(namex)
        d = self._read()
        d.addCallback(lambda children: children.has_key(name))
        return d

    def _get(self, children, name):
        child = children.get(name)
        if child is None:
            raise NoSuchChildError(name)
        return child[0]

    def _get_with_metadata(self, children, name):
        child = children.get(name)
        if child is None:
            raise NoSuchChildError(name)
        return child

    def get(self, namex):
        """I return a Deferred that fires with the named child node,
        which is an IFilesystemNode."""
        name = normalize(namex)
        d = self._read()
        d.addCallback(self._get, name)
        return d

    def get_child_and_metadata(self, namex):
        """I return a Deferred that fires with the (node, metadata) pair for
        the named child. The node is an IFilesystemNode, and the metadata
        is a dictionary."""
        name = normalize(namex)
        d = self._read()
        d.addCallback(self._get_with_metadata, name)
        return d

    def get_metadata_for(self, namex):
        name = normalize(namex)
        d = self._read()
        d.addCallback(lambda children: children[name][1])
        return d

    def set_metadata_for(self, namex, metadata):
        name = normalize(namex)
        if self.is_readonly():
            return defer.fail(NotWriteableError())
        assert isinstance(metadata, dict)
        s = MetadataSetter(self, name, metadata,
                           create_readonly_node=self._create_readonly_node)
        d = self._node.modify(s.modify)
        d.addCallback(lambda res: self)
        return d

    def get_child_at_path(self, pathx):
        """Transform a child path into an IFilesystemNode.

        I perform a recursive series of 'get' operations to find the named
        descendant node. I return a Deferred that fires with the node, or
        errbacks with IndexError if the node could not be found.

        The path can be either a single string (slash-separated) or a list of
        path-name elements.
        """
        d = self.get_child_and_metadata_at_path(pathx)
        d.addCallback(lambda node_and_metadata: node_and_metadata[0])
        return d

    def get_child_and_metadata_at_path(self, pathx):
        """Transform a child path into an IFilesystemNode and
        a metadata dictionary from the last edge that was traversed.
        """

        if not pathx:
            return defer.succeed((self, {}))
        if isinstance(pathx, (list, tuple)):
            pass
        else:
            pathx = pathx.split("/")
        for p in pathx:
            assert isinstance(p, unicode), p
        childnamex = pathx[0]
        remaining_pathx = pathx[1:]
        if remaining_pathx:
            d = self.get(childnamex)
            d.addCallback(lambda node:
                          node.get_child_and_metadata_at_path(remaining_pathx))
            return d
        d = self.get_child_and_metadata(childnamex)
        return d

    def set_uri(self, namex, writecap, readcap, metadata=None, overwrite=True):
        precondition(isinstance(writecap, (str,type(None))), writecap)
        precondition(isinstance(readcap, (str,type(None))), readcap)

        # We now allow packing unknown nodes, provided they are valid
        # for this type of directory.
        child_node = self._create_and_validate_node(writecap, readcap, namex)
        d = self.set_node(namex, child_node, metadata, overwrite)
        d.addCallback(lambda res: child_node)
        return d

    def set_children(self, entries, overwrite=True):
        # this takes URIs
        a = Adder(self, overwrite=overwrite,
                  create_readonly_node=self._create_readonly_node)
        for (namex, e) in entries.iteritems():
            assert isinstance(namex, unicode), namex
            if len(e) == 2:
                writecap, readcap = e
                metadata = None
            else:
                assert len(e) == 3
                writecap, readcap, metadata = e
            precondition(isinstance(writecap, (str,type(None))), writecap)
            precondition(isinstance(readcap, (str,type(None))), readcap)

            # We now allow packing unknown nodes, provided they are valid
            # for this type of directory.
            child_node = self._create_and_validate_node(writecap, readcap, namex)
            a.set_node(namex, child_node, metadata)
        d = self._node.modify(a.modify)
        d.addCallback(lambda ign: self)
        return d

    def set_node(self, namex, child, metadata=None, overwrite=True):
        """I add a child at the specific name. I return a Deferred that fires
        when the operation finishes. This Deferred will fire with the child
        node that was just added. I will replace any existing child of the
        same name.

        If this directory node is read-only, the Deferred will errback with a
        NotWriteableError."""

        precondition(IFilesystemNode.providedBy(child), child)

        if self.is_readonly():
            return defer.fail(NotWriteableError())
        assert IFilesystemNode.providedBy(child), child
        a = Adder(self, overwrite=overwrite,
                  create_readonly_node=self._create_readonly_node)
        a.set_node(namex, child, metadata)
        d = self._node.modify(a.modify)
        d.addCallback(lambda res: child)
        return d

    def set_nodes(self, entries, overwrite=True):
        precondition(isinstance(entries, dict), entries)
        if self.is_readonly():
            return defer.fail(NotWriteableError())
        a = Adder(self, entries, overwrite=overwrite,
                  create_readonly_node=self._create_readonly_node)
        d = self._node.modify(a.modify)
        d.addCallback(lambda res: self)
        return d


    def add_file(self, namex, uploadable, metadata=None, overwrite=True, progress=None):
        """I upload a file (using the given IUploadable), then attach the
        resulting FileNode to the directory at the given name. I return a
        Deferred that fires (with the IFileNode of the uploaded file) when
        the operation completes."""
        with ADD_FILE(name=namex, metadata=metadata, overwrite=overwrite).context():
            name = normalize(namex)
            if self.is_readonly():
                d = DeferredContext(defer.fail(NotWriteableError()))
            else:
                # XXX should pass reactor arg
                d = DeferredContext(self._uploader.upload(uploadable, progress=progress))
                d.addCallback(lambda results:
                              self._create_and_validate_node(results.get_uri(), None,
                                                             name))
                d.addCallback(lambda node:
                              self.set_node(name, node, metadata, overwrite))

        return d.addActionFinish()

    def delete(self, namex, must_exist=True, must_be_directory=False, must_be_file=False):
        """I remove the child at the specific name. I return a Deferred that
        fires (with the node just removed) when the operation finishes."""
        if self.is_readonly():
            return defer.fail(NotWriteableError())
        deleter = Deleter(self, namex, must_exist=must_exist,
                          must_be_directory=must_be_directory, must_be_file=must_be_file)
        d = self._node.modify(deleter.modify)
        d.addCallback(lambda res: deleter.old_child)
        return d

    # XXX: Too many arguments? Worthwhile to break into mutable/immutable?
    def create_subdirectory(self, namex, initial_children={}, overwrite=True,
                            mutable=True, mutable_version=None, metadata=None):
        name = normalize(namex)
        if self.is_readonly():
            return defer.fail(NotWriteableError())
        if mutable:
            if mutable_version:
                d = self._nodemaker.create_new_mutable_directory(initial_children,
                                                                 version=mutable_version)
            else:
                d = self._nodemaker.create_new_mutable_directory(initial_children)
        else:
            # mutable version doesn't make sense for immmutable directories.
            assert mutable_version is None
            d = self._nodemaker.create_immutable_directory(initial_children)
        def _created(child):
            entries = {name: (child, metadata)}
            a = Adder(self, entries, overwrite=overwrite,
                      create_readonly_node=self._create_readonly_node)
            d = self._node.modify(a.modify)
            d.addCallback(lambda res: child)
            return d
        d.addCallback(_created)
        return d

    def move_child_to(self, current_child_namex, new_parent,
                      new_child_namex=None, overwrite=True):
        """
        I take one of my child links and move it to a new parent. The child
        link is referenced by name. In the new parent, the child link will live
        at 'new_child_namex', which defaults to 'current_child_namex'. I return
        a Deferred that fires when the operation finishes.
        'new_child_namex' and 'current_child_namex' need not be normalized.

        The overwrite parameter may be True (overwrite any existing child),
        False (error if the new child link already exists), or "only-files"
        (error if the new child link exists and points to a directory).
        """
        if self.is_readonly() or new_parent.is_readonly():
            return defer.fail(NotWriteableError())

        current_child_name = normalize(current_child_namex)
        if new_child_namex is None:
            new_child_name = current_child_name
        else:
            new_child_name = normalize(new_child_namex)

        from_uri = self.get_write_uri()
        if new_parent.get_write_uri() == from_uri and new_child_name == current_child_name:
            # needed for correctness, otherwise we would delete the child
            return defer.succeed("redundant rename/relink")

        d = self.get_child_and_metadata(current_child_name)
        def _got_child(child_and_metadata):
            (child, metadata) = child_and_metadata
            return new_parent.set_node(new_child_name, child, metadata,
                                       overwrite=overwrite)
        d.addCallback(_got_child)
        d.addCallback(lambda child: self.delete(current_child_name))
        return d


    def deep_traverse(self, walker):
        """Perform a recursive walk, using this dirnode as a root, notifying
        the 'walker' instance of everything I encounter.

        I call walker.enter_directory(parent, children) once for each dirnode
        I visit, immediately after retrieving the list of children. I pass in
        the parent dirnode and the dict of childname->(childnode,metadata).
        This function should *not* traverse the children: I will do that.
        enter_directory() is most useful for the deep-stats number that
        counts how large a directory is.

        I call walker.add_node(node, path) for each node (both files and
        directories) I can reach. Most work should be done here.

        I avoid loops by keeping track of verifier-caps and refusing to call
        walker.add_node() or traverse a node that I've seen before. This
        means that any file or directory will only be given to the walker
        once. If files or directories are referenced multiple times by a
        directory structure, this may appear to under-count or miss some of
        them.

        I return a Monitor which can be used to wait for the operation to
        finish, learn about its progress, or cancel the operation.
        """

        # this is just a tree-walker, except that following each edge
        # requires a Deferred. We used to use a ConcurrencyLimiter to limit
        # fanout to 10 simultaneous operations, but the memory load of the
        # queued operations was excessive (in one case, with 330k dirnodes,
        # it caused the process to run into the 3.0GB-ish per-process 32bit
        # linux memory limit, and crashed). So we use a single big Deferred
        # chain, and do a strict depth-first traversal, one node at a time.
        # This can be slower, because we aren't pipelining directory reads,
        # but it brought the memory footprint down by roughly 50%.

        monitor = Monitor()
        walker.set_monitor(monitor)

        found = set([self.get_verify_cap()])
        d = self._deep_traverse_dirnode(self, [], walker, monitor, found)
        d.addCallback(lambda ignored: walker.finish())
        d.addBoth(monitor.finish)
        d.addErrback(lambda f: None)

        return monitor

    def _deep_traverse_dirnode(self, node, path, walker, monitor, found):
        # process this directory, then walk its children
        monitor.raise_if_cancelled()
        d = defer.maybeDeferred(walker.add_node, node, path)
        d.addCallback(lambda ignored: node.list())
        d.addCallback(self._deep_traverse_dirnode_children, node, path,
                      walker, monitor, found)
        return d

    def _deep_traverse_dirnode_children(self, children, parent, path,
                                        walker, monitor, found):
        monitor.raise_if_cancelled()
        d = defer.maybeDeferred(walker.enter_directory, parent, children)
        # we process file-like children first, so we can drop their FileNode
        # objects as quickly as possible. Tests suggest that a FileNode (held
        # in the client's nodecache) consumes about 2440 bytes. dirnodes (not
        # in the nodecache) seem to consume about 2000 bytes.
        dirkids = []
        filekids = []
        for name, (child, metadata) in sorted(children.iteritems()):
            childpath = path + [name]
            if isinstance(child, UnknownNode):
                walker.add_node(child, childpath)
                continue
            verifier = child.get_verify_cap()
            # allow LIT files (for which verifier==None) to be processed
            if (verifier is not None) and (verifier in found):
                continue
            found.add(verifier)
            if IDirectoryNode.providedBy(child):
                dirkids.append( (child, childpath) )
            else:
                filekids.append( (child, childpath) )
        for i, (child, childpath) in enumerate(filekids):
            d.addCallback(lambda ignored, child=child, childpath=childpath:
                          walker.add_node(child, childpath))
            # to work around the Deferred tail-recursion problem
            # (specifically the defer.succeed flavor) requires us to avoid
            # doing more than 158 LIT files in a row. We insert a turn break
            # once every 100 files (LIT or CHK) to preserve some stack space
            # for other code. This is a different expression of the same
            # Twisted problem as in #237.
            if i % 100 == 99:
                d.addCallback(lambda ignored: fireEventually())
        for (child, childpath) in dirkids:
            d.addCallback(lambda ignored, child=child, childpath=childpath:
                          self._deep_traverse_dirnode(child, childpath,
                                                      walker, monitor,
                                                      found))
        return d


    def build_manifest(self):
        """Return a Monitor, with a ['status'] that will be a list of (path,
        cap) tuples, for all nodes (directories and files) reachable from
        this one."""
        walker = ManifestWalker(self)
        return self.deep_traverse(walker)

    def start_deep_stats(self):
        # Since deep_traverse tracks verifier caps, we avoid double-counting
        # children for which we've got both a write-cap and a read-cap
        return self.deep_traverse(DeepStats(self))

    def start_deep_check(self, verify=False, add_lease=False):
        return self.deep_traverse(DeepChecker(self, verify, repair=False, add_lease=add_lease))

    def start_deep_check_and_repair(self, verify=False, add_lease=False):
        return self.deep_traverse(DeepChecker(self, verify, repair=True, add_lease=add_lease))


class ManifestWalker(DeepStats):
    def __init__(self, origin):
        DeepStats.__init__(self, origin)
        self.manifest = []
        self.storage_index_strings = set()
        self.verifycaps = set()

    def add_node(self, node, path):
        self.manifest.append( (tuple(path), node.get_uri()) )
        si = node.get_storage_index()
        if si:
            self.storage_index_strings.add(base32.b2a(si))
        v = node.get_verify_cap()
        if v:
            self.verifycaps.add(v.to_string())
        return DeepStats.add_node(self, node, path)

    def get_results(self):
        stats = DeepStats.get_results(self)
        return {"manifest": self.manifest,
                "verifycaps": self.verifycaps,
                "storage-index": self.storage_index_strings,
                "stats": stats,
                }


class DeepChecker(object):
    def __init__(self, root, verify, repair, add_lease):
        root_si = root.get_storage_index()
        if root_si:
            root_si_base32 = base32.b2a(root_si)
        else:
            root_si_base32 = ""
        self._lp = log.msg(format="deep-check starting (%(si)s),"
                           " verify=%(verify)s, repair=%(repair)s",
                           si=root_si_base32, verify=verify, repair=repair)
        self._verify = verify
        self._repair = repair
        self._add_lease = add_lease
        if repair:
            self._results = DeepCheckAndRepairResults(root_si)
        else:
            self._results = DeepCheckResults(root_si)
        self._stats = DeepStats(root)

    def set_monitor(self, monitor):
        self.monitor = monitor
        monitor.set_status(self._results)

    def add_node(self, node, childpath):
        if self._repair:
            d = node.check_and_repair(self.monitor, self._verify, self._add_lease)
            d.addCallback(self._results.add_check_and_repair, childpath)
        else:
            d = node.check(self.monitor, self._verify, self._add_lease)
            d.addCallback(self._results.add_check, childpath)
        d.addCallback(lambda ignored: self._stats.add_node(node, childpath))
        return d

    def enter_directory(self, parent, children):
        return self._stats.enter_directory(parent, children)

    def finish(self):
        log.msg("deep-check done", parent=self._lp)
        self._results.update_stats(self._stats.get_results())
        return self._results


# use client.create_dirnode() to make one of these
