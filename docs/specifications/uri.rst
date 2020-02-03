﻿.. -*- coding: utf-8-with-signature -*-

==========
Tahoe URIs
==========

1.  `File URIs`_

    1. `CHK URIs`_
    2. `LIT URIs`_
    3. `Mutable File URIs`_

2.  `Directory URIs`_
3.  `Internal Usage of URIs`_

Each file and directory in a Tahoe-LAFS file store is described by a "URI".
There are different kinds of URIs for different kinds of objects, and there
are different kinds of URIs to provide different kinds of access to those
objects. Each URI is a string representation of a "capability" or "cap", and
there are read-caps, write-caps, verify-caps, and others.

Each URI provides both ``location`` and ``identification`` properties.
``location`` means that holding the URI is sufficient to locate the data it
represents (this means it contains a storage index or a lookup key, whatever
is necessary to find the place or places where the data is being kept).
``identification`` means that the URI also serves to validate the data: an
attacker who wants to trick you into into using the wrong data will be
limited in their abilities by the identification properties of the URI.

Some URIs are subsets of others. In particular, if you know a URI which
allows you to modify some object, you can produce a weaker read-only URI and
give it to someone else, and they will be able to read that object but not
modify it. Directories, for example, have a read-cap which is derived from
the write-cap: anyone with read/write access to the directory can produce a
limited URI that grants read-only access, but not the other way around.

src/allmydata/uri.py is the main place where URIs are processed. It is
the authoritative definition point for all the the URI types described
herein.

File URIs
=========

The lowest layer of the Tahoe architecture (the "key-value store") is
reponsible for mapping URIs to data. This is basically a distributed
hash table, in which the URI is the key, and some sequence of bytes is
the value.

There are two kinds of entries in this table: immutable and mutable. For
immutable entries, the URI represents a fixed chunk of data. The URI itself
is derived from the data when it is uploaded into the grid, and can be used
to locate and download that data from the grid at some time in the future.

For mutable entries, the URI identifies a "slot" or "container", which can be
filled with different pieces of data at different times.

It is important to note that the values referenced by these URIs are just
sequences of bytes, and that **no** filenames or other metadata is retained at
this layer. The file store layer (which sits above the key-value store layer)
is entirely responsible for directories and filenames and the like.

CHK URIs
--------

CHK (Content Hash Keyed) files are immutable sequences of bytes. They are
uploaded in a distributed fashion using a "storage index" (for the "location"
property), and encrypted using a "read key". A secure hash of the data is
computed to help validate the data afterwards (providing the "identification"
property). All of these pieces, plus information about the file's size and
the number of shares into which it has been distributed, are put into the
"CHK" uri. The storage index is derived by hashing the read key (using a
tagged SHA-256d hash, then truncated to 128 bits), so it does not need to be
physically present in the URI.

The current format for CHK URIs is the concatenation of the following
strings::

 URI:CHK:(key):(hash):(needed-shares):(total-shares):(size)

Where (key) is the base32 encoding of the 16-byte AES read key, (hash) is the
base32 encoding of the SHA-256 hash of the URI Extension Block,
(needed-shares) is an ascii decimal representation of the number of shares
required to reconstruct this file, (total-shares) is the same representation
of the total number of shares created, and (size) is an ascii decimal
representation of the size of the data represented by this URI. All base32
encodings are expressed in lower-case, with the trailing '=' signs removed.

For example, the following is a CHK URI, generated from a previous version of
the contents of :doc:`architecture.rst<../architecture>`::

 URI:CHK:ihrbeov7lbvoduupd4qblysj7a:bg5agsdt62jb34hxvxmdsbza6do64f4fg5anxxod2buttbo6udzq:3:10:28733

Historical note: The name "CHK" is somewhat inaccurate and continues to be
used for historical reasons. "Content Hash Key" means that the encryption key
is derived by hashing the contents, which gives the useful property that
encoding the same file twice will result in the same URI. However, this is an
optional step: by passing a different flag to the appropriate API call, Tahoe
will generate a random encryption key instead of hashing the file: this gives
the useful property that the URI or storage index does not reveal anything
about the file's contents (except filesize), which improves privacy. The
URI:CHK: prefix really indicates that an immutable file is in use, without
saying anything about how the key was derived.


LIT URIs
--------

LITeral files are also an immutable sequence of bytes, but they are so short
that the data is stored inside the URI itself. These are used for files of 55
bytes or shorter, which is the point at which the LIT URI is the same length
as a CHK URI would be.

LIT URIs do not require an upload or download phase, as their data is stored
directly in the URI.

The format of a LIT URI is simply a fixed prefix concatenated with the base32
encoding of the file's data::

 URI:LIT:bjuw4y3movsgkidbnrwg26lemf2gcl3xmvrc6kropbuhi3lmbi

The LIT URI for an empty file is "URI:LIT:", and the LIT URI for a 5-byte
file that contains the string "hello" is "URI:LIT:nbswy3dp".

Mutable File URIs
-----------------

The other kind of DHT entry is the "mutable slot", in which the URI names a
container to which data can be placed and retrieved without changing the
identity of the container.

These slots have write-caps (which allow read/write access), read-caps (which
only allow read-access), and verify-caps (which allow a file checker/repairer
to confirm that the contents exist, but does not let it decrypt the
contents).

Mutable slots use public key technology to provide data integrity, and put a
hash of the public key in the URI. As a result, the data validation is
limited to confirming that the data retrieved matches *some* data that was
uploaded in the past, but not _which_ version of that data.

The format of the write-cap for mutable files is::

 URI:SSK:(writekey):(fingerprint)

Where (writekey) is the base32 encoding of the 16-byte AES encryption key
that is used to encrypt the RSA private key, and (fingerprint) is the base32
encoded 32-byte SHA-256 hash of the RSA public key. For more details about
the way these keys are used, please see :doc:`mutable`.

The format for mutable read-caps is::

 URI:SSK-RO:(readkey):(fingerprint)

The read-cap is just like the write-cap except it contains the other AES
encryption key: the one used for encrypting the mutable file's contents. This
second key is derived by hashing the writekey, which allows the holder of a
write-cap to produce a read-cap, but not the other way around. The
fingerprint is the same in both caps.

Historical note: the "SSK" prefix is a perhaps-inaccurate reference to
"Sub-Space Keys" from the Freenet project, which uses a vaguely similar
structure to provide mutable file access.


Directory URIs
==============

The key-value store layer provides a mapping from URI to data. To turn this
into a graph of directories and files, the "file store" layer (which sits on
top of the key-value store layer) needs to keep track of "directory nodes",
or "dirnodes" for short. :doc:`dirnodes` describes how these work.

Dirnodes are contained inside mutable files, and are thus simply a particular
way to interpret the contents of these files. As a result, a directory
write-cap looks a lot like a mutable-file write-cap::

 URI:DIR2:(writekey):(fingerprint)

Likewise directory read-caps (which provide read-only access to the
directory) look much like mutable-file read-caps::

 URI:DIR2-RO:(readkey):(fingerprint)

Historical note: the "DIR2" prefix is used because the non-distributed
dirnodes in earlier Tahoe releases had already claimed the "DIR" prefix.


Internal Usage of URIs
======================

The classes in source:src/allmydata/uri.py are used to pack and unpack these
various kinds of URIs. Three Interfaces are defined (IURI, IFileURI, and
IDirnodeURI) which are implemented by these classes, and string-to-URI-class
conversion routines have been registered as adapters, so that code which
wants to extract e.g. the size of a CHK or LIT uri can do::

 print IFileURI(uri).get_size()

If the URI does not represent a CHK or LIT uri (for example, if it was for a
directory instead), the adaptation will fail, raising a TypeError inside the
IFileURI() call.

Several utility methods are provided on these objects. The most important is
``to_string()``, which returns the string form of the URI. Therefore
``IURI(uri).to_string == uri`` is true for any valid URI. See the IURI class
in source:src/allmydata/interfaces.py for more details.

