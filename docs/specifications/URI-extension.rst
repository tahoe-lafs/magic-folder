﻿.. -*- coding: utf-8-with-signature -*-

===================
URI Extension Block
===================

This block is a serialized dictionary with string keys and string values
(some of which represent numbers, some of which are SHA-256 hashes). All
buckets hold an identical copy. The hash of the serialized data is kept in
the URI.

The download process must obtain a valid copy of this data before any
decoding can take place. The download process must also obtain other data
before incremental validation can be performed. Full-file validation (for
clients who do not wish to do incremental validation) can be performed solely
with the data from this block.

At the moment, this data block contains the following keys (and an estimate
on their sizes)::

 size                5
 segment_size        7
 num_segments        2
 needed_shares       2
 total_shares        3

 codec_name          3
 codec_params        5+1+2+1+3=12
 tail_codec_params   12

 share_root_hash     32 (binary) or 52 (base32-encoded) each
 plaintext_hash
 plaintext_root_hash
 crypttext_hash
 crypttext_root_hash

Some pieces are needed elsewhere (size should be visible without pulling the
block, the Tahoe3 algorithm needs total_shares to find the right peers, all
peer selection algorithms need needed_shares to ask a minimal set of peers).
Some pieces are arguably redundant but are convenient to have present
(test_encode.py makes use of num_segments).

The rule for this data block is that it should be a constant size for all
files, regardless of file size. Therefore hash trees (which have a size that
depends linearly upon the number of segments) are stored elsewhere in the
bucket, with only the hash tree root stored in this data block.

This block will be serialized as follows::

 assert that all keys match ^[a-zA-z_\-]+$
 sort all the keys lexicographically
 for k in keys:
  write("%s:" % k)
  write(netstring(data[k]))


Serialized size::

 dense binary (but decimal) packing: 160+46=206
 including 'key:' (185) and netstring (6*3+7*4=46) on values: 231
 including 'key:%d\n' (185+13=198) and printable values (46+5*52=306)=504

We'll go with the 231-sized block, and provide a tool to dump it as text if
we really want one.
