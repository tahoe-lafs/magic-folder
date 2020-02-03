﻿.. -*- coding: utf-8-with-signature -*-

==========================
The Tahoe REST-ful Web API
==========================

1.  `Enabling the web-API port`_
2.  `Basic Concepts: GET, PUT, DELETE, POST`_
3.  `URLs`_

    1. `Child Lookup`_

4.  `Slow Operations, Progress, and Cancelling`_
5.  `Programmatic Operations`_

    1. `Reading a file`_
    2. `Writing/Uploading a File`_
    3. `Creating a New Directory`_
    4. `Getting Information About a File Or Directory (as JSON)`_
    5. `Attaching an Existing File or Directory by its read- or write-cap`_
    6. `Adding Multiple Files or Directories to a Parent Directory at Once`_
    7. `Unlinking a File or Directory`_

6.  `Browser Operations: Human-Oriented Interfaces`_

    1.  `Viewing a Directory (as HTML)`_
    2.  `Viewing/Downloading a File`_
    3.  `Getting Information About a File Or Directory (as HTML)`_
    4.  `Creating a Directory`_
    5.  `Uploading a File`_
    6.  `Attaching an Existing File Or Directory (by URI)`_
    7.  `Unlinking a Child`_
    8.  `Renaming a Child`_
    9.  `Relinking ("Moving") a Child`_
    10. `Other Utilities`_
    11. `Debugging and Testing Features`_

7.  `Other Useful Pages`_
8.  `Static Files in /public_html`_
9.  `Safety and Security Issues -- Names vs. URIs`_
10. `Concurrency Issues`_
11. `Access Blacklist`_


Enabling the web-API port
=========================

Every Tahoe node is capable of running a built-in HTTP server. To enable
this, just write a port number into the "[node]web.port" line of your node's
tahoe.cfg file. For example, writing "web.port = 3456" into the "[node]"
section of $NODEDIR/tahoe.cfg will cause the node to run a webserver on port
3456.

This string is actually a Twisted "strports" specification, meaning you can
get more control over the interface to which the server binds by supplying
additional arguments. For more details, see the documentation on
`twisted.application.strports`_.

Writing "tcp:3456:interface=127.0.0.1" into the web.port line does the same
but binds to the loopback interface, ensuring that only the programs on the
local host can connect. Using "ssl:3456:privateKey=mykey.pem:certKey=cert.pem"
runs an SSL server.

This webport can be set when the node is created by passing a --webport
option to the 'tahoe create-node' command. By default, the node listens on
port 3456, on the loopback (127.0.0.1) interface.

.. _twisted.application.strports: https://twistedmatrix.com/documents/current/api/twisted.application.strports.html


Basic Concepts: GET, PUT, DELETE, POST
======================================

As described in :doc:`../architecture`, each file and directory in a
Tahoe-LAFS file store is referenced by an identifier that combines the
designation of the object with the authority to do something with it (such as
read or modify the contents). This identifier is called a "read-cap" or
"write-cap", depending upon whether it enables read-only or read-write
access. These "caps" are also referred to as URIs (which may be confusing
because they are not currently RFC3986_-compliant URIs).

The Tahoe web-based API is "REST-ful", meaning it implements the concepts of
"REpresentational State Transfer": the original scheme by which the World
Wide Web was intended to work. Each object (file or directory) is referenced
by a URL that includes the read- or write- cap. HTTP methods (GET, PUT, and
DELETE) are used to manipulate these objects. You can think of the URL as a
noun, and the method as a verb.

In REST, the GET method is used to retrieve information about an object, or
to retrieve some representation of the object itself. When the object is a
file, the basic GET method will simply return the contents of that file.
Other variations (generally implemented by adding query parameters to the
URL) will return information about the object, such as metadata. GET
operations are required to have no side-effects.

PUT is used to upload new objects into the file store, or to replace an
existing link or the contents of a mutable file. DELETE is used to unlink
objects from directories. Both PUT and DELETE are required to be idempotent:
performing the same operation multiple times must have the same side-effects
as only performing it once.

POST is used for more complicated actions that cannot be expressed as a GET,
PUT, or DELETE. POST operations can be thought of as a method call: sending
some message to the object referenced by the URL. In Tahoe, POST is also used
for operations that must be triggered by an HTML form (including upload and
unlinking), because otherwise a regular web browser has no way to accomplish
these tasks. In general, everything that can be done with a PUT or DELETE can
also be done with a POST.

Tahoe-LAFS' web API is designed for two different kinds of consumer. The
first is a program that needs to manipulate the file store. Such programs are
expected to use the RESTful interface described above. The second is a human
using a standard web browser to work with the file store. This user is
presented with a series of HTML pages with links to download files, and forms
that use POST actions to upload, rename, and unlink files.

When an error occurs, the HTTP response code will be set to an appropriate
400-series code (like 404 Not Found for an unknown childname, or 400 Bad Request
when the parameters to a web-API operation are invalid), and the HTTP response
body will usually contain a few lines of explanation as to the cause of the
error and possible responses. Unusual exceptions may result in a 500 Internal
Server Error as a catch-all, with a default response body containing
a Nevow-generated HTML-ized representation of the Python exception stack trace
that caused the problem. CLI programs which want to copy the response body to
stderr should provide an "Accept: text/plain" header to their requests to get
a plain text stack trace instead. If the Accept header contains ``*/*``, or
``text/*``, or text/html (or if there is no Accept header), HTML tracebacks will
be generated.

.. _RFC3986: https://tools.ietf.org/html/rfc3986


URLs
====

Tahoe uses a variety of read- and write- caps to identify files and
directories. The most common of these is the "immutable file read-cap", which
is used for most uploaded files. These read-caps look like the following::

 URI:CHK:ime6pvkaxuetdfah2p2f35pe54:4btz54xk3tew6nd4y2ojpxj4m6wxjqqlwnztgre6gnjgtucd5r4a:3:10:202

The next most common is a "directory write-cap", which provides both read and
write access to a directory, and look like this::

 URI:DIR2:djrdkfawoqihigoett4g6auz6a:jx5mplfpwexnoqff7y5e4zjus4lidm76dcuarpct7cckorh2dpgq

There are also "directory read-caps", which start with "URI:DIR2-RO:", and
give read-only access to a directory. Finally there are also mutable file
read- and write- caps, which start with "URI:SSK", and give access to mutable
files.

(Later versions of Tahoe will make these strings shorter, and will remove the
unfortunate colons, which must be escaped when these caps are embedded in
URLs.)

To refer to any Tahoe object through the web API, you simply need to combine
a prefix (which indicates the HTTP server to use) with the cap (which
indicates which object inside that server to access). Since the default Tahoe
webport is 3456, the most common prefix is one that will use a local node
listening on this port::

 http://127.0.0.1:3456/uri/ + $CAP

So, to access the directory named above, the URL would be::

 http://127.0.0.1:3456/uri/URI%3ADIR2%3Adjrdkfawoqihigoett4g6auz6a%3Ajx5mplfpwexnoqff7y5e4zjus4lidm76dcuarpct7cckorh2dpgq/

(note that the colons in the directory-cap are url-encoded into "%3A"
sequences).

Likewise, to access the file named above, use::

 http://127.0.0.1:3456/uri/URI%3ACHK%3Aime6pvkaxuetdfah2p2f35pe54%3A4btz54xk3tew6nd4y2ojpxj4m6wxjqqlwnztgre6gnjgtucd5r4a%3A3%3A10%3A202

In the rest of this document, we'll use "$DIRCAP" as shorthand for a read-cap
or write-cap that refers to a directory, and "$FILECAP" to abbreviate a cap
that refers to a file (whether mutable or immutable). So those URLs above can
be abbreviated as::

 http://127.0.0.1:3456/uri/$DIRCAP/
 http://127.0.0.1:3456/uri/$FILECAP

The operation summaries below will abbreviate these further, by eliding the
server prefix. They will be displayed like this::

 /uri/$DIRCAP/
 /uri/$FILECAP

/cap can be used as a synonym for /uri.  If interoperability with older web-API
servers is required, /uri should be used.

Child Lookup
------------

Tahoe directories contain named child entries, just like directories in a
regular local filesystem. These child entries, called "dirnodes", consist of
a name, metadata, a write slot, and a read slot. The write and read slots
normally contain a write-cap and read-cap referring to the same object, which
can be either a file or a subdirectory. The write slot may be empty
(actually, both may be empty, but that is unusual).

If you have a Tahoe URL that refers to a directory, and want to reference a
named child inside it, just append the child name to the URL. For example, if
our sample directory contains a file named "welcome.txt", we can refer to
that file with::

 http://127.0.0.1:3456/uri/$DIRCAP/welcome.txt

(or http://127.0.0.1:3456/uri/URI%3ADIR2%3Adjrdkfawoqihigoett4g6auz6a%3Ajx5mplfpwexnoqff7y5e4zjus4lidm76dcuarpct7cckorh2dpgq/welcome.txt)

Multiple levels of subdirectories can be handled this way::

 http://127.0.0.1:3456/uri/$DIRCAP/tahoe-source/docs/architecture.rst

In this document, when we need to refer to a URL that references a file using
this child-of-some-directory format, we'll use the following string::

 /uri/$DIRCAP/[SUBDIRS../]FILENAME

The "[SUBDIRS../]" part means that there are zero or more (optional)
subdirectory names in the middle of the URL. The "FILENAME" at the end means
that this whole URL refers to a file of some sort, rather than to a
directory.

When we need to refer specifically to a directory in this way, we'll write::

 /uri/$DIRCAP/[SUBDIRS../]SUBDIR


Note that all components of pathnames in URLs are required to be UTF-8
encoded, so "resume.doc" (with an acute accent on both E's) would be accessed
with::

 http://127.0.0.1:3456/uri/$DIRCAP/r%C3%A9sum%C3%A9.doc

Also note that the filenames inside upload POST forms are interpreted using
whatever character set was provided in the conventional '_charset' field, and
defaults to UTF-8 if not otherwise specified. The JSON representation of each
directory contains native Unicode strings. Tahoe directories are specified to
contain Unicode filenames, and cannot contain binary strings that are not
representable as such.

All Tahoe operations that refer to existing files or directories must include
a suitable read- or write- cap in the URL: the web-API server won't add one
for you. If you don't know the cap, you can't access the file. This allows
the security properties of Tahoe caps to be extended across the web-API
interface.


Slow Operations, Progress, and Cancelling
=========================================

Certain operations can be expected to take a long time. The "t=deep-check",
described below, will recursively visit every file and directory reachable
from a given starting point, which can take minutes or even hours for
extremely large directory structures. A single long-running HTTP request is a
fragile thing: proxies, NAT boxes, browsers, and users may all grow impatient
with waiting and give up on the connection.

For this reason, long-running operations have an "operation handle", which
can be used to poll for status/progress messages while the operation
proceeds. This handle can also be used to cancel the operation. These handles
are created by the client, and passed in as a an "ophandle=" query argument
to the POST or PUT request which starts the operation. The following
operations can then be used to retrieve status:

``GET /operations/$HANDLE?output=HTML   (with or without t=status)``

``GET /operations/$HANDLE?output=JSON   (same)``

 These two retrieve the current status of the given operation. Each operation
 presents a different sort of information, but in general the page retrieved
 will indicate:

 * whether the operation is complete, or if it is still running
 * how much of the operation is complete, and how much is left, if possible

 Note that the final status output can be quite large: a deep-manifest of a
 directory structure with 300k directories and 200k unique files is about
 275MB of JSON, and might take two minutes to generate. For this reason, the
 full status is not provided until the operation has completed.

 The HTML form will include a meta-refresh tag, which will cause a regular
 web browser to reload the status page about 60 seconds later. This tag will
 be removed once the operation has completed.

 There may be more status information available under
 /operations/$HANDLE/$ETC : i.e., the handle forms the root of a URL space.

``POST /operations/$HANDLE?t=cancel``

 This terminates the operation, and returns an HTML page explaining what was
 cancelled. If the operation handle has already expired (see below), this
 POST will return a 404, which indicates that the operation is no longer
 running (either it was completed or terminated). The response body will be
 the same as a GET /operations/$HANDLE on this operation handle, and the
 handle will be expired immediately afterwards.

The operation handle will eventually expire, to avoid consuming an unbounded
amount of memory. The handle's time-to-live can be reset at any time, by
passing a retain-for= argument (with a count of seconds) to either the
initial POST that starts the operation, or the subsequent GET request which
asks about the operation. For example, if a 'GET
/operations/$HANDLE?output=JSON&retain-for=600' query is performed, the
handle will remain active for 600 seconds (10 minutes) after the GET was
received.

In addition, if the GET includes a release-after-complete=True argument, and
the operation has completed, the operation handle will be released
immediately.

If a retain-for= argument is not used, the default handle lifetimes are:

 * handles will remain valid at least until their operation finishes
 * uncollected handles for finished operations (i.e. handles for
   operations that have finished but for which the GET page has not been
   accessed since completion) will remain valid for four days, or for
   the total time consumed by the operation, whichever is greater.
 * collected handles (i.e. the GET page has been retrieved at least once
   since the operation completed) will remain valid for one day.

Many "slow" operations can begin to use unacceptable amounts of memory when
operating on large directory structures. The memory usage increases when the
ophandle is polled, as the results must be copied into a JSON string, sent
over the wire, then parsed by a client. So, as an alternative, many "slow"
operations have streaming equivalents. These equivalents do not use operation
handles. Instead, they emit line-oriented status results immediately. Client
code can cancel the operation by simply closing the HTTP connection.


Programmatic Operations
=======================

Now that we know how to build URLs that refer to files and directories in a
Tahoe-LAFS file store, what sorts of operations can we do with those URLs?
This section contains a catalog of GET, PUT, DELETE, and POST operations that
can be performed on these URLs. This set of operations are aimed at programs
that use HTTP to communicate with a Tahoe node. A later section describes
operations that are intended for web browsers.


Reading a File
--------------

``GET /uri/$FILECAP``

``GET /uri/$DIRCAP/[SUBDIRS../]FILENAME``

 This will retrieve the contents of the given file. The HTTP response body
 will contain the sequence of bytes that make up the file.

 The "Range:" header can be used to restrict which portions of the file are
 returned (see RFC 2616 section 14.35.1 "Byte Ranges"), however Tahoe only
 supports a single "bytes" range and never provides a
 ``multipart/byteranges`` response. An attempt to begin a read past the end
 of the file will provoke a 416 Requested Range Not Satisfiable error, but
 normal overruns (reads which start at the beginning or middle and go beyond
 the end) are simply truncated.

 To view files in a web browser, you may want more control over the
 Content-Type and Content-Disposition headers. Please see the next section
 "Browser Operations", for details on how to modify these URLs for that
 purpose.


Writing/Uploading a File
------------------------

``PUT /uri/$FILECAP``

``PUT /uri/$DIRCAP/[SUBDIRS../]FILENAME``

 Upload a file, using the data from the HTTP request body, and add whatever
 child links and subdirectories are necessary to make the file available at
 the given location. Once this operation succeeds, a GET on the same URL will
 retrieve the same contents that were just uploaded. This will create any
 necessary intermediate subdirectories.

 To use the /uri/$FILECAP form, $FILECAP must be a write-cap for a mutable file.

 In the /uri/$DIRCAP/[SUBDIRS../]FILENAME form, if the target file is a
 writeable mutable file, that file's contents will be overwritten
 in-place. If it is a read-cap for a mutable file, an error will occur.
 If it is an immutable file, the old file will be discarded, and a new
 one will be put in its place. If the target file is a writable mutable
 file, you may also specify an "offset" parameter -- a byte offset that
 determines where in the mutable file the data from the HTTP request
 body is placed. This operation is relatively efficient for MDMF mutable
 files, and is relatively inefficient (but still supported) for SDMF
 mutable files. If no offset parameter is specified, then the entire
 file is replaced with the data from the HTTP request body. For an
 immutable file, the "offset" parameter is not valid.

 When creating a new file, you can control the type of file created by
 specifying a format= argument in the query string. format=MDMF creates an
 MDMF mutable file. format=SDMF creates an SDMF mutable file. format=CHK
 creates an immutable file. The value of the format argument is
 case-insensitive. If no format is specified, the newly-created file will be
 immutable (but see below).

 For compatibility with previous versions of Tahoe-LAFS, the web-API will
 also accept a mutable=true argument in the query string. If mutable=true is
 given, then the new file will be mutable, and its format will be the default
 mutable file format, as configured by the [client]mutable.format option of
 tahoe.cfg on the Tahoe-LAFS node hosting the webapi server. Use of
 mutable=true is discouraged; new code should use format= instead of
 mutable=true (unless it needs to be compatible with web-API servers older
 than v1.9.0). If neither format= nor mutable=true are given, the
 newly-created file will be immutable.

 This returns the file-cap of the resulting file. If a new file was created
 by this method, the HTTP response code (as dictated by rfc2616) will be set
 to 201 CREATED. If an existing file was replaced or modified, the response
 code will be 200 OK.

 Note that the 'curl -T localfile http://127.0.0.1:3456/uri/$DIRCAP/foo.txt'
 command can be used to invoke this operation.

``PUT /uri``

 This uploads a file, and produces a file-cap for the contents, but does not
 attach the file into the file store. No directories will be modified by
 this operation. The file-cap is returned as the body of the HTTP response.

 This method accepts format= and mutable=true as query string arguments, and
 interprets those arguments in the same way as the linked forms of PUT
 described immediately above.

Creating a New Directory
------------------------

``POST /uri?t=mkdir``

``PUT /uri?t=mkdir``

 Create a new empty directory and return its write-cap as the HTTP response
 body. This does not make the newly created directory visible from the
 file store. The "PUT" operation is provided for backwards compatibility:
 new code should use POST.

 This supports a format= argument in the query string. The format=
 argument, if specified, controls the format of the directory. format=MDMF
 indicates that the directory should be stored as an MDMF file; format=SDMF
 indicates that the directory should be stored as an SDMF file. The value of
 the format= argument is case-insensitive. If no format= argument is
 given, the directory's format is determined by the default mutable file
 format, as configured on the Tahoe-LAFS node responding to the request.

``POST /uri?t=mkdir-with-children``

 Create a new directory, populated with a set of child nodes, and return its
 write-cap as the HTTP response body. The new directory is not attached to
 any other directory: the returned write-cap is the only reference to it.

 The format of the directory can be controlled with the format= argument in
 the query string, as described above.

 Initial children are provided as the body of the POST form (this is more
 efficient than doing separate mkdir and set_children operations). If the
 body is empty, the new directory will be empty. If not empty, the body will
 be interpreted as a UTF-8 JSON-encoded dictionary of children with which the
 new directory should be populated, using the same format as would be
 returned in the 'children' value of the t=json GET request, described below.
 Each dictionary key should be a child name, and each value should be a list
 of [TYPE, PROPDICT], where PROPDICT contains "rw_uri", "ro_uri", and
 "metadata" keys (all others are ignored). For example, the PUT request body
 could be::

  {
    "Fran\u00e7ais": [ "filenode", {
        "ro_uri": "URI:CHK:...",
        "metadata": {
          "ctime": 1202777696.7564139,
          "mtime": 1202777696.7564139,
          "tahoe": {
            "linkcrtime": 1202777696.7564139,
            "linkmotime": 1202777696.7564139
            } } } ],
    "subdir":  [ "dirnode", {
        "rw_uri": "URI:DIR2:...",
        "ro_uri": "URI:DIR2-RO:...",
        "metadata": {
          "ctime": 1202778102.7589991,
          "mtime": 1202778111.2160511,
          "tahoe": {
            "linkcrtime": 1202777696.7564139,
            "linkmotime": 1202777696.7564139
          } } } ]
  }

 For forward-compatibility, a mutable directory can also contain caps in
 a format that is unknown to the web-API server. When such caps are retrieved
 from a mutable directory in a "ro_uri" field, they will be prefixed with
 the string "ro.", indicating that they must not be decoded without
 checking that they are read-only. The "ro." prefix must not be stripped
 off without performing this check. (Future versions of the web-API server
 will perform it where necessary.)

 If both the "rw_uri" and "ro_uri" fields are present in a given PROPDICT,
 and the web-API server recognizes the rw_uri as a write cap, then it will
 reset the ro_uri to the corresponding read cap and discard the original
 contents of ro_uri (in order to ensure that the two caps correspond to the
 same object and that the ro_uri is in fact read-only). However this may not
 happen for caps in a format unknown to the web-API server. Therefore, when
 writing a directory the web-API client should ensure that the contents
 of "rw_uri" and "ro_uri" for a given PROPDICT are a consistent
 (write cap, read cap) pair if possible. If the web-API client only has
 one cap and does not know whether it is a write cap or read cap, then
 it is acceptable to set "rw_uri" to that cap and omit "ro_uri". The
 client must not put a write cap into a "ro_uri" field.

 The metadata may have a "no-write" field. If this is set to true in the
 metadata of a link, it will not be possible to open that link for writing
 via the SFTP frontend; see :doc:`FTP-and-SFTP` for details. Also, if the
 "no-write" field is set to true in the metadata of a link to a mutable
 child, it will cause the link to be diminished to read-only.

 Note that the web-API-using client application must not provide the
 "Content-Type: multipart/form-data" header that usually accompanies HTML
 form submissions, since the body is not formatted this way. Doing so will
 cause a server error as the lower-level code misparses the request body.

 Child file names should each be expressed as a Unicode string, then used as
 keys of the dictionary. The dictionary should then be converted into JSON,
 and the resulting string encoded into UTF-8. This UTF-8 bytestring should
 then be used as the POST body.

``POST /uri?t=mkdir-immutable``

 Like t=mkdir-with-children above, but the new directory will be
 deep-immutable. This means that the directory itself is immutable, and that
 it can only contain objects that are treated as being deep-immutable, like
 immutable files, literal files, and deep-immutable directories.

 For forward-compatibility, a deep-immutable directory can also contain caps
 in a format that is unknown to the web-API server. When such caps are retrieved
 from a deep-immutable directory in a "ro_uri" field, they will be prefixed
 with the string "imm.", indicating that they must not be decoded without
 checking that they are immutable. The "imm." prefix must not be stripped
 off without performing this check. (Future versions of the web-API server
 will perform it where necessary.)

 The cap for each child may be given either in the "rw_uri" or "ro_uri"
 field of the PROPDICT (not both). If a cap is given in the "rw_uri" field,
 then the web-API server will check that it is an immutable read-cap of a
 *known* format, and give an error if it is not. If a cap is given in the
 "ro_uri" field, then the web-API server will still check whether known
 caps are immutable, but for unknown caps it will simply assume that the
 cap can be stored, as described above. Note that an attacker would be
 able to store any cap in an immutable directory, so this check when
 creating the directory is only to help non-malicious clients to avoid
 accidentally giving away more authority than intended.

 A non-empty request body is mandatory, since after the directory is created,
 it will not be possible to add more children to it.

``POST /uri/$DIRCAP/[SUBDIRS../]SUBDIR?t=mkdir``

``PUT /uri/$DIRCAP/[SUBDIRS../]SUBDIR?t=mkdir``

 Create new directories as necessary to make sure that the named target
 ($DIRCAP/SUBDIRS../SUBDIR) is a directory. This will create additional
 intermediate mutable directories as necessary. If the named target directory
 already exists, this will make no changes to it.

 If the final directory is created, it will be empty.

 This accepts a format= argument in the query string, which controls the
 format of the named target directory, if it does not already exist. format=
 is interpreted in the same way as in the POST /uri?t=mkdir form. Note that
 format= only controls the format of the named target directory;
 intermediate directories, if created, are created based on the default
 mutable type, as configured on the Tahoe-LAFS server responding to the
 request.

 This operation will return an error if a blocking file is present at any of
 the parent names, preventing the server from creating the necessary parent
 directory; or if it would require changing an immutable directory.

 The write-cap of the new directory will be returned as the HTTP response
 body.

``POST /uri/$DIRCAP/[SUBDIRS../]SUBDIR?t=mkdir-with-children``

 Like /uri?t=mkdir-with-children, but the final directory is created as a
 child of an existing mutable directory. This will create additional
 intermediate mutable directories as necessary. If the final directory is
 created, it will be populated with initial children from the POST request
 body, as described above.

 This accepts a format= argument in the query string, which controls the
 format of the target directory, if the target directory is created as part
 of the operation. format= is interpreted in the same way as in the POST/
 uri?t=mkdir-with-children operation. Note that format= only controls the
 format of the named target directory; intermediate directories, if created,
 are created using the default mutable type setting, as configured on the
 Tahoe-LAFS server responding to the request.

 This operation will return an error if a blocking file is present at any of
 the parent names, preventing the server from creating the necessary parent
 directory; or if it would require changing an immutable directory; or if
 the immediate parent directory already has a a child named SUBDIR.

``POST /uri/$DIRCAP/[SUBDIRS../]SUBDIR?t=mkdir-immutable``

 Like /uri?t=mkdir-immutable, but the final directory is created as a child
 of an existing mutable directory. The final directory will be deep-immutable,
 and will be populated with the children specified as a JSON dictionary in
 the POST request body.

 In Tahoe 1.6 this operation creates intermediate mutable directories if
 necessary, but that behaviour should not be relied on; see ticket #920.

 This operation will return an error if the parent directory is immutable,
 or already has a child named SUBDIR.

``POST /uri/$DIRCAP/[SUBDIRS../]?t=mkdir&name=NAME``

 Create a new empty mutable directory and attach it to the given existing
 directory. This will create additional intermediate directories as necessary.

 This accepts a format= argument in the query string, which controls the
 format of the named target directory, if it does not already exist. format=
 is interpreted in the same way as in the POST /uri?t=mkdir form. Note that
 format= only controls the format of the named target directory;
 intermediate directories, if created, are created based on the default
 mutable type, as configured on the Tahoe-LAFS server responding to the
 request.

 This operation will return an error if a blocking file is present at any of
 the parent names, preventing the server from creating the necessary parent
 directory, or if it would require changing any immutable directory.

 The URL of this operation points to the parent of the bottommost new directory,
 whereas the /uri/$DIRCAP/[SUBDIRS../]SUBDIR?t=mkdir operation above has a URL
 that points directly to the bottommost new directory.

``POST /uri/$DIRCAP/[SUBDIRS../]?t=mkdir-with-children&name=NAME``

 Like /uri/$DIRCAP/[SUBDIRS../]?t=mkdir&name=NAME, but the new directory will
 be populated with initial children via the POST request body. This command
 will create additional intermediate mutable directories as necessary.

 This accepts a format= argument in the query string, which controls the
 format of the target directory, if the target directory is created as part
 of the operation. format= is interpreted in the same way as in the POST/
 uri?t=mkdir-with-children operation. Note that format= only controls the
 format of the named target directory; intermediate directories, if created,
 are created using the default mutable type setting, as configured on the
 Tahoe-LAFS server responding to the request.

 This operation will return an error if a blocking file is present at any of
 the parent names, preventing the server from creating the necessary parent
 directory; or if it would require changing an immutable directory; or if
 the immediate parent directory already has a a child named NAME.

 Note that the name= argument must be passed as a queryarg, because the POST
 request body is used for the initial children JSON.

``POST /uri/$DIRCAP/[SUBDIRS../]?t=mkdir-immutable&name=NAME``

 Like /uri/$DIRCAP/[SUBDIRS../]?t=mkdir-with-children&name=NAME, but the
 final directory will be deep-immutable. The children are specified as a
 JSON dictionary in the POST request body. Again, the name= argument must be
 passed as a queryarg.

 In Tahoe 1.6 this operation creates intermediate mutable directories if
 necessary, but that behaviour should not be relied on; see ticket #920.

 This operation will return an error if the parent directory is immutable,
 or already has a child named NAME.


Getting Information About a File Or Directory (as JSON)
-------------------------------------------------------

``GET /uri/$FILECAP?t=json``

``GET /uri/$DIRCAP?t=json``

``GET /uri/$DIRCAP/[SUBDIRS../]SUBDIR?t=json``

``GET /uri/$DIRCAP/[SUBDIRS../]FILENAME?t=json``

 This returns a machine-parseable JSON-encoded description of the given
 object. The JSON always contains a list, and the first element of the list is
 always a flag that indicates whether the referenced object is a file or a
 directory. If it is a capability to a file, then the information includes
 file size and URI, like this::

  GET /uri/$FILECAP?t=json :

   [ "filenode", {
      "ro_uri": file_uri,
      "verify_uri": verify_uri,
      "size": bytes,
      "mutable": false,
      "format": "CHK"
     } ]

 If it is a capability to a directory followed by a path from that directory
 to a file, then the information also includes metadata from the link to the
 file in the parent directory, like this::

  GET /uri/$DIRCAP/[SUBDIRS../]FILENAME?t=json

   [ "filenode", {
      "ro_uri": file_uri,
      "verify_uri": verify_uri,
      "size": bytes,
      "mutable": false,
      "format": "CHK",
      "metadata": {
       "ctime": 1202777696.7564139,
       "mtime": 1202777696.7564139,
       "tahoe": {
        "linkcrtime": 1202777696.7564139,
        "linkmotime": 1202777696.7564139
       } } } ]

 If it is a directory, then it includes information about the children of
 this directory, as a mapping from child name to a set of data about the
 child (the same data that would appear in a corresponding GET?t=json of the
 child itself). The child entries also include metadata about each child,
 including link-creation- and link-change- timestamps. The output looks like
 this::

  GET /uri/$DIRCAP?t=json :
  GET /uri/$DIRCAP/[SUBDIRS../]SUBDIR?t=json :

   [ "dirnode", {
     "rw_uri": read_write_uri,
     "ro_uri": read_only_uri,
     "verify_uri": verify_uri,
     "mutable": true,
     "format": "SDMF",
     "children": {
      "foo.txt": [ "filenode",
                   {
                     "ro_uri": uri,
                     "size": bytes,
                     "metadata": {
                       "ctime": 1202777696.7564139,
                       "mtime": 1202777696.7564139,
                       "tahoe": {
                         "linkcrtime": 1202777696.7564139,
                         "linkmotime": 1202777696.7564139
                       } } } ],
      "subdir":  [ "dirnode",
                   {
                     "rw_uri": rwuri,
                     "ro_uri": rouri,
                     "metadata": {
                       "ctime": 1202778102.7589991,
                       "mtime": 1202778111.2160511,
                       "tahoe": {
                         "linkcrtime": 1202777696.7564139,
                         "linkmotime": 1202777696.7564139
                       } } } ]
      } } ]

 In the above example, note how 'children' is a dictionary in which the keys
 are child names and the values depend upon whether the child is a file or a
 directory. The value is mostly the same as the JSON representation of the
 child object (except that directories do not recurse -- the "children"
 entry of the child is omitted, and the directory view includes the metadata
 that is stored on the directory edge).

 The rw_uri field will be present in the information about a directory
 if and only if you have read-write access to that directory. The verify_uri
 field will be present if and only if the object has a verify-cap
 (non-distributed LIT files do not have verify-caps).

 If the cap is of an unknown format, then the file size and verify_uri will
 not be available::

  GET /uri/$UNKNOWNCAP?t=json :

   [ "unknown", {
       "ro_uri": unknown_read_uri
       } ]

  GET /uri/$DIRCAP/[SUBDIRS../]UNKNOWNCHILDNAME?t=json :

   [ "unknown", {
       "rw_uri": unknown_write_uri,
       "ro_uri": unknown_read_uri,
       "mutable": true,
       "metadata": {
         "ctime": 1202777696.7564139,
         "mtime": 1202777696.7564139,
         "tahoe": {
           "linkcrtime": 1202777696.7564139,
           "linkmotime": 1202777696.7564139
         } } } ]

 As in the case of file nodes, the metadata will only be present when the
 capability is to a directory followed by a path. The "mutable" field is also
 not always present; when it is absent, the mutability of the object is not
 known.

About the metadata
``````````````````

The value of the 'tahoe':'linkmotime' key is updated whenever a link to a
child is set. The value of the 'tahoe':'linkcrtime' key is updated whenever
a link to a child is created -- i.e. when there was not previously a link
under that name.

Note however, that if the edge in the Tahoe-LAFS file store points to a
mutable file and the contents of that mutable file is changed, then the
'tahoe':'linkmotime' value on that edge will *not* be updated, since the
edge itself wasn't updated -- only the mutable file was.

The timestamps are represented as a number of seconds since the UNIX epoch
(1970-01-01 00:00:00 UTC), with leap seconds not being counted in the long
term.

In Tahoe earlier than v1.4.0, 'mtime' and 'ctime' keys were populated
instead of the 'tahoe':'linkmotime' and 'tahoe':'linkcrtime' keys. Starting
in Tahoe v1.4.0, the 'linkmotime'/'linkcrtime' keys in the 'tahoe' sub-dict
are populated. However, prior to Tahoe v1.7beta, a bug caused the 'tahoe'
sub-dict to be deleted by web-API requests in which new metadata is
specified, and not to be added to existing child links that lack it.

From Tahoe v1.7.0 onward, the 'mtime' and 'ctime' fields are no longer
populated or updated (see ticket #924), except by "tahoe backup" as
explained below. For backward compatibility, when an existing link is
updated and 'tahoe':'linkcrtime' is not present in the previous metadata
but 'ctime' is, the old value of 'ctime' is used as the new value of
'tahoe':'linkcrtime'.

The reason we added the new fields in Tahoe v1.4.0 is that there is a
"set_children" API (described below) which you can use to overwrite the
values of the 'mtime'/'ctime' pair, and this API is used by the
"tahoe backup" command (in Tahoe v1.3.0 and later) to set the 'mtime' and
'ctime' values when backing up files from a local filesystem into the
Tahoe-LAFS file store. As of Tahoe v1.4.0, the set_children API cannot be
used to set anything under the 'tahoe' key of the metadata dict -- if you
include 'tahoe' keys in your 'metadata' arguments then it will silently
ignore those keys.

Therefore, if the 'tahoe' sub-dict is present, you can rely on the
'linkcrtime' and 'linkmotime' values therein to have the semantics described
above. (This is assuming that only official Tahoe clients have been used to
write those links, and that their system clocks were set to what you expected
-- there is nothing preventing someone from editing their Tahoe client or
writing their own Tahoe client which would overwrite those values however
they like, and there is nothing to constrain their system clock from taking
any value.)

When an edge is created or updated by "tahoe backup", the 'mtime' and
'ctime' keys on that edge are set as follows:

* 'mtime' is set to the timestamp read from the local filesystem for the
  "mtime" of the local file in question, which means the last time the
  contents of that file were changed.

* On Windows, 'ctime' is set to the creation timestamp for the file
  read from the local filesystem. On other platforms, 'ctime' is set to
  the UNIX "ctime" of the local file, which means the last time that
  either the contents or the metadata of the local file was changed.

There are several ways that the 'ctime' field could be confusing:

1. You might be confused about whether it reflects the time of the creation
   of a link in the Tahoe-LAFS file store (by a version of Tahoe < v1.7.0)
   or a timestamp copied in by "tahoe backup" from a local filesystem.

2. You might be confused about whether it is a copy of the file creation
   time (if "tahoe backup" was run on a Windows system) or of the last
   contents-or-metadata change (if "tahoe backup" was run on a different
   operating system).

3. You might be confused by the fact that changing the contents of a
   mutable file in Tahoe doesn't have any effect on any links pointing at
   that file in any directories, although "tahoe backup" sets the link
   'ctime'/'mtime' to reflect timestamps about the local file corresponding
   to the Tahoe file to which the link points.

4. Also, quite apart from Tahoe, you might be confused about the meaning
   of the "ctime" in UNIX local filesystems, which people sometimes think
   means file creation time, but which actually means, in UNIX local
   filesystems, the most recent time that the file contents or the file
   metadata (such as owner, permission bits, extended attributes, etc.)
   has changed. Note that although "ctime" does not mean file creation time
   in UNIX, links created by a version of Tahoe prior to v1.7.0, and never
   written by "tahoe backup", will have 'ctime' set to the link creation
   time.


Attaching an Existing File or Directory by its read- or write-cap
-----------------------------------------------------------------

``PUT /uri/$DIRCAP/[SUBDIRS../]CHILDNAME?t=uri``

 This attaches a child object (either a file or directory) to a specified
 location in the Tahoe-LAFS file store. The child object is referenced by its
 read- or write- cap, as provided in the HTTP request body. This will create
 intermediate directories as necessary.

 This is similar to a UNIX hardlink: by referencing a previously-uploaded file
 (or previously-created directory) instead of uploading/creating a new one,
 you can create two references to the same object.

 The read- or write- cap of the child is provided in the body of the HTTP
 request, and this same cap is returned in the response body.

 The default behavior is to overwrite any existing object at the same
 location. To prevent this (and make the operation return an error instead
 of overwriting), add a "replace=false" argument, as "?t=uri&replace=false".
 With replace=false, this operation will return an HTTP 409 "Conflict" error
 if there is already an object at the given location, rather than
 overwriting the existing object. To allow the operation to overwrite a
 file, but return an error when trying to overwrite a directory, use
 "replace=only-files" (this behavior is closer to the traditional UNIX "mv"
 command). Note that "true", "t", and "1" are all synonyms for "True", and
 "false", "f", and "0" are synonyms for "False", and the parameter is
 case-insensitive.

 Note that this operation does not take its child cap in the form of
 separate "rw_uri" and "ro_uri" fields. Therefore, it cannot accept a
 child cap in a format unknown to the web-API server, unless its URI
 starts with "ro." or "imm.". This restriction is necessary because the
 server is not able to attenuate an unknown write cap to a read cap.
 Unknown URIs starting with "ro." or "imm.", on the other hand, are
 assumed to represent read caps. The client should not prefix a write
 cap with "ro." or "imm." and pass it to this operation, since that
 would result in granting the cap's write authority to holders of the
 directory read cap.


Adding Multiple Files or Directories to a Parent Directory at Once
------------------------------------------------------------------

``POST /uri/$DIRCAP/[SUBDIRS..]?t=set_children``

``POST /uri/$DIRCAP/[SUBDIRS..]?t=set-children``    (Tahoe >= v1.6)

 This command adds multiple children to a directory in a single operation.
 It reads the request body and interprets it as a JSON-encoded description
 of the child names and read/write-caps that should be added.

 The body should be a JSON-encoded dictionary, in the same format as the
 "children" value returned by the "GET /uri/$DIRCAP?t=json" operation
 described above. In this format, each key is a child names, and the
 corresponding value is a tuple of (type, childinfo). "type" is ignored, and
 "childinfo" is a dictionary that contains "rw_uri", "ro_uri", and
 "metadata" keys. You can take the output of "GET /uri/$DIRCAP1?t=json" and
 use it as the input to "POST /uri/$DIRCAP2?t=set_children" to make DIR2
 look very much like DIR1 (except for any existing children of DIR2 that
 were not overwritten, and any existing "tahoe" metadata keys as described
 below).

 When the set_children request contains a child name that already exists in
 the target directory, this command defaults to overwriting that child with
 the new value (both child cap and metadata, but if the JSON data does not
 contain a "metadata" key, the old child's metadata is preserved). The
 command takes a boolean "overwrite=" query argument to control this
 behavior. If you use "?t=set_children&overwrite=false", then an attempt to
 replace an existing child will instead cause an error.

 Any "tahoe" key in the new child's "metadata" value is ignored. Any
 existing "tahoe" metadata is preserved. The metadata["tahoe"] value is
 reserved for metadata generated by the tahoe node itself. The only two keys
 currently placed here are "linkcrtime" and "linkmotime". For details, see
 the section above entitled "Getting Information About a File Or Directory (as
 JSON)", in the "About the metadata" subsection.

 Note that this command was introduced with the name "set_children", which
 uses an underscore rather than a hyphen as other multi-word command names
 do. The variant with a hyphen is now accepted, but clients that desire
 backward compatibility should continue to use "set_children".


Unlinking a File or Directory
-----------------------------

``DELETE /uri/$DIRCAP/[SUBDIRS../]CHILDNAME``

 This removes the given name from its parent directory. CHILDNAME is the
 name to be removed, and $DIRCAP/SUBDIRS.. indicates the directory that will
 be modified.

 Note that this does not actually delete the file or directory that the name
 points to from the tahoe grid -- it only unlinks the named reference from
 this directory. If there are other names in this directory or in other
 directories that point to the resource, then it will remain accessible
 through those paths. Even if all names pointing to this object are removed
 from their parent directories, then someone with possession of its read-cap
 can continue to access the object through that cap.

 The object will only become completely unreachable once 1: there are no
 reachable directories that reference it, and 2: nobody is holding a read-
 or write- cap to the object. (This behavior is very similar to the way
 hardlinks and anonymous files work in traditional UNIX filesystems).

 This operation will not modify more than a single directory. Intermediate
 directories which were implicitly created by PUT or POST methods will *not*
 be automatically removed by DELETE.

 This method returns the file- or directory- cap of the object that was just
 removed.


Browser Operations: Human-oriented interfaces
=============================================

This section describes the HTTP operations that provide support for humans
running a web browser. Most of these operations use HTML forms that use POST
to drive the Tahoe-LAFS node. This section is intended for HTML authors who
want to write web pages containing user interfaces for manipulating the
Tahoe-LAFS file store.

Note that for all POST operations, the arguments listed can be provided
either as URL query arguments or as form body fields. URL query arguments are
separated from the main URL by "?", and from each other by "&". For example,
"POST /uri/$DIRCAP?t=upload&mutable=true". Form body fields are usually
specified by using <input type="hidden"> elements. For clarity, the
descriptions below display the most significant arguments as URL query args.


Viewing a Directory (as HTML)
-----------------------------

``GET /uri/$DIRCAP/[SUBDIRS../]``

 This returns an HTML page, intended to be displayed to a human by a web
 browser, which contains HREF links to all files and directories reachable
 from this directory. These HREF links do not have a t= argument, meaning
 that a human who follows them will get pages also meant for a human. It also
 contains forms to upload new files, and to unlink files and directories
 from their parent directory. Those forms use POST methods to do their job.


Viewing/Downloading a File
--------------------------

``GET /uri/$FILECAP``

``GET /uri/$DIRCAP/[SUBDIRS../]FILENAME``

``GET /named/$FILECAP/FILENAME``

 These will retrieve the contents of the given file. The HTTP response body
 will contain the sequence of bytes that make up the file.

 The ``/named/`` form is an alternative to ``/uri/$FILECAP`` which makes it
 easier to get the correct filename. The Tahoe server will provide the
 contents of the given file, with a Content-Type header derived from the
 given filename. This form is used to get browsers to use the "Save Link As"
 feature correctly, and also helps command-line tools like "wget" and "curl"
 use the right filename. Note that this form can *only* be used with file
 caps; it is an error to use a directory cap after the /named/ prefix.

 URLs may also use /file/$FILECAP/FILENAME as a synonym for
 /named/$FILECAP/FILENAME. The use of "/file/" is deprecated in favor of
 "/named/" and support for "/file/" will be removed in a future release of
 Tahoe-LAFS.

 If you use the first form (``/uri/$FILECAP``) and want the HTTP response to
 include a useful Content-Type header, add a "filename=foo" query argument,
 like "GET /uri/$FILECAP?filename=foo.jpg". The bare "GET /uri/$FILECAP" does
 not give the Tahoe node enough information to determine a Content-Type
 (since LAFS immutable files are merely sequences of bytes, not typed and
 named file objects).

 If the URL has both filename= and "save=true" in the query arguments, then
 the server to add a "Content-Disposition: attachment" header, along with a
 filename= parameter. When a user clicks on such a link, most browsers will
 offer to let the user save the file instead of displaying it inline (indeed,
 most browsers will refuse to display it inline). "true", "t", "1", and other
 case-insensitive equivalents are all treated the same.

 Character-set handling in URLs and HTTP headers is a :ref:`dubious
 art<urls-and-utf8>`. For maximum compatibility, Tahoe simply copies the
 bytes from the filename= argument into the Content-Disposition header's
 filename= parameter, without trying to interpret them in any particular way.


Getting Information About a File Or Directory (as HTML)
-------------------------------------------------------

``GET /uri/$FILECAP?t=info``

``GET /uri/$DIRCAP/?t=info``

``GET /uri/$DIRCAP/[SUBDIRS../]SUBDIR/?t=info``

``GET /uri/$DIRCAP/[SUBDIRS../]FILENAME?t=info``

 This returns a human-oriented HTML page with more detail about the selected
 file or directory object. This page contains the following items:

 * object size
 * storage index
 * JSON representation
 * raw contents (text/plain)
 * access caps (URIs): verify-cap, read-cap, write-cap (for mutable objects)
 * check/verify/repair form
 * deep-check/deep-size/deep-stats/manifest (for directories)
 * replace-contents form (for mutable files)


Creating a Directory
--------------------

``POST /uri?t=mkdir``

 This creates a new empty directory, but does not attach it to any other
 directory in the Tahoe-LAFS file store.

 If a "redirect_to_result=true" argument is provided, then the HTTP response
 will cause the web browser to be redirected to a /uri/$DIRCAP page that
 gives access to the newly-created directory. If you bookmark this page,
 you'll be able to get back to the directory again in the future. This is the
 recommended way to start working with a Tahoe server: create a new unlinked
 directory (using redirect_to_result=true), then bookmark the resulting
 /uri/$DIRCAP page. There is a "create directory" button on the Welcome page
 to invoke this action.

 This accepts a format= argument in the query string. Refer to the
 documentation of the PUT /uri?t=mkdir operation in `Creating A
 New Directory`_ for information on the behavior of the format= argument.

 If "redirect_to_result=true" is not provided (or is given a value of
 "false"), then the HTTP response body will simply be the write-cap of the
 new directory.

``POST /uri/$DIRCAP/[SUBDIRS../]?t=mkdir&name=CHILDNAME``

 This creates a new empty directory as a child of the designated SUBDIR. This
 will create additional intermediate directories as necessary.

 This accepts a format= argument in the query string. Refer to the
 documentation of POST /uri/$DIRCAP/[SUBDIRS../]?t=mkdir&name=CHILDNAME in
 `Creating a New Directory`_ for information on the behavior of the format=
 argument.

 If a "when_done=URL" argument is provided, the HTTP response will cause the
 web browser to redirect to the given URL. This provides a convenient way to
 return the browser to the directory that was just modified. Without a
 when_done= argument, the HTTP response will simply contain the write-cap of
 the directory that was just created.


Uploading a File
----------------

``POST /uri?t=upload``

 This uploads a file, and produces a file-cap for the contents, but does not
 attach the file to any directory in the Tahoe-LAFS file store. That is, no
 directories will be modified by this operation.

 The file must be provided as the "file" field of an HTML encoded form body,
 produced in response to an HTML form like this::

  <form action="/uri" method="POST" enctype="multipart/form-data">
   <input type="hidden" name="t" value="upload" />
   <input type="file" name="file" />
   <input type="submit" value="Upload Unlinked" />
  </form>

 If a "when_done=URL" argument is provided, the response body will cause the
 browser to redirect to the given URL. If the when_done= URL has the string
 "%(uri)s" in it, that string will be replaced by a URL-escaped form of the
 newly created file-cap. (Note that without this substitution, there is no
 way to access the file that was just uploaded).

 The default (in the absence of when_done=) is to return an HTML page that
 describes the results of the upload. This page will contain information
 about which storage servers were used for the upload, how long each
 operation took, etc.

 This accepts format= and mutable=true query string arguments. Refer to
 `Writing/Uploading a File`_ for information on the behavior of format= and
 mutable=true.

``POST /uri/$DIRCAP/[SUBDIRS../]?t=upload``

 This uploads a file, and attaches it as a new child of the given directory,
 which must be mutable. The file must be provided as the "file" field of an
 HTML-encoded form body, produced in response to an HTML form like this::

  <form action="." method="POST" enctype="multipart/form-data">
   <input type="hidden" name="t" value="upload" />
   <input type="file" name="file" />
   <input type="submit" value="Upload" />
  </form>

 A "name=" argument can be provided to specify the new child's name,
 otherwise it will be taken from the "filename" field of the upload form
 (most web browsers will copy the last component of the original file's
 pathname into this field). To avoid confusion, name= is not allowed to
 contain a slash.

 If there is already a child with that name, and it is a mutable file, then
 its contents are replaced with the data being uploaded. If it is not a
 mutable file, the default behavior is to remove the existing child before
 creating a new one. To prevent this (and make the operation return an error
 instead of overwriting the old child), add a "replace=false" argument, as
 "?t=upload&replace=false". With replace=false, this operation will return an
 HTTP 409 "Conflict" error if there is already an object at the given
 location, rather than overwriting the existing object. Note that "true",
 "t", and "1" are all synonyms for "True", and "false", "f", and "0" are
 synonyms for "False". the parameter is case-insensitive.

 This will create additional intermediate directories as necessary, although
 since it is expected to be triggered by a form that was retrieved by "GET
 /uri/$DIRCAP/[SUBDIRS../]", it is likely that the parent directory will
 already exist.

 This accepts format= and mutable=true query string arguments. Refer to
 `Writing/Uploading a File`_ for information on the behavior of format= and
 mutable=true.

 If a "when_done=URL" argument is provided, the HTTP response will cause the
 web browser to redirect to the given URL. This provides a convenient way to
 return the browser to the directory that was just modified. Without a
 when_done= argument, the HTTP response will simply contain the file-cap of
 the file that was just uploaded (a write-cap for mutable files, or a
 read-cap for immutable files).

``POST /uri/$DIRCAP/[SUBDIRS../]FILENAME?t=upload``

 This also uploads a file and attaches it as a new child of the given
 directory, which must be mutable. It is a slight variant of the previous
 operation, as the URL refers to the target file rather than the parent
 directory. It is otherwise identical: this accepts mutable= and when_done=
 arguments too.

``POST /uri/$FILECAP?t=upload``

 This modifies the contents of an existing mutable file in-place. An error is
 signalled if $FILECAP does not refer to a mutable file. It behaves just like
 the "PUT /uri/$FILECAP" form, but uses a POST for the benefit of HTML forms
 in a web browser.


Attaching An Existing File Or Directory (by URI)
------------------------------------------------

``POST /uri/$DIRCAP/[SUBDIRS../]?t=uri&name=CHILDNAME&uri=CHILDCAP``

 This attaches a given read- or write- cap "CHILDCAP" to the designated
 directory, with a specified child name. This behaves much like the PUT t=uri
 operation, and is a lot like a UNIX hardlink. It is subject to the same
 restrictions as that operation on the use of cap formats unknown to the
 web-API server.

 This will create additional intermediate directories as necessary, although
 since it is expected to be triggered by a form that was retrieved by "GET
 /uri/$DIRCAP/[SUBDIRS../]", it is likely that the parent directory will
 already exist.

 This accepts the same replace= argument as POST t=upload.


Unlinking a Child
-----------------

``POST /uri/$DIRCAP/[SUBDIRS../]?t=delete&name=CHILDNAME``

``POST /uri/$DIRCAP/[SUBDIRS../]?t=unlink&name=CHILDNAME``    (Tahoe >= v1.9)

 This instructs the node to remove a child object (file or subdirectory) from
 the given directory, which must be mutable. Note that the entire subtree is
 unlinked from the parent. Unlike deleting a subdirectory in a UNIX local
 filesystem, the subtree need not be empty; if it isn't, then other references
 into the subtree will see that the child subdirectories are not modified by
 this operation. Only the link from the given directory to its child is severed.

 In Tahoe-LAFS v1.9.0 and later, t=unlink can be used as a synonym for t=delete.
 If interoperability with older web-API servers is required, t=delete should
 be used.


Renaming a Child
----------------

``POST /uri/$DIRCAP/[SUBDIRS../]?t=rename&from_name=OLD&to_name=NEW``

 This instructs the node to rename a child of the given directory, which must
 be mutable. This has a similar effect to removing the child, then adding the
 same child-cap under the new name, except that it preserves metadata. This
 operation cannot move the child to a different directory.

 The default behavior is to overwrite any existing link at the destination
 (replace=true). To prevent this (and make the operation return an error
 instead of overwriting), add a "replace=false" argument. With replace=false,
 this operation will return an HTTP 409 "Conflict" error if the destination
 is not the same link as the source and there is already a link at the
 destination, rather than overwriting the existing link. To allow the
 operation to overwrite a link to a file, but return an HTTP 409 error when
 trying to overwrite a link to a directory, use "replace=only-files" (this
 behavior is closer to the traditional UNIX "mv" command). Note that "true",
 "t", and "1" are all synonyms for "True"; "false", "f", and "0" are synonyms
 for "False"; and the parameter is case-insensitive.


Relinking ("Moving") a Child
----------------------------

``POST /uri/$DIRCAP/[SUBDIRS../]?t=relink&from_name=OLD&to_dir=$NEWDIRCAP/[NEWSUBDIRS../]&to_name=NEW``
 ``[&replace=true|false|only-files]``    (Tahoe >= v1.10)

 This instructs the node to move a child of the given source directory, into
 a different directory and/or to a different name. The command is named
 ``relink`` because what it does is add a new link to the child from the new
 location, then remove the old link. Nothing is actually "moved": the child
 is still reachable through any path from which it was formerly reachable,
 and the storage space occupied by its ciphertext is not affected.

 The source and destination directories must be writeable. If ``to_dir`` is
 not present, the child link is renamed within the same directory. If
 ``to_name`` is not present then it defaults to ``from_name``. If the
 destination link (directory and name) is the same as the source link, the
 operation has no effect.

 Metadata from the source directory entry is preserved. Multiple levels of
 descent in the source and destination paths are supported.

 This operation will return an HTTP 404 "Not Found" error if
 ``$DIRCAP/[SUBDIRS../]``, the child being moved, or the destination
 directory does not exist. It will return an HTTP 400 "Bad Request" error
 if any entry in the source or destination paths is not a directory.

 The default behavior is to overwrite any existing link at the destination
 (replace=true). To prevent this (and make the operation return an error
 instead of overwriting), add a "replace=false" argument. With replace=false,
 this operation will return an HTTP 409 "Conflict" error if the destination
 is not the same link as the source and there is already a link at the
 destination, rather than overwriting the existing link. To allow the
 operation to overwrite a link to a file, but return an HTTP 409 error when
 trying to overwrite a link to a directory, use "replace=only-files" (this
 behavior is closer to the traditional UNIX "mv" command). Note that "true",
 "t", and "1" are all synonyms for "True"; "false", "f", and "0" are synonyms
 for "False"; and the parameter is case-insensitive.

 When relinking into a different directory, for safety, the child link is
 not removed from the old directory until it has been successfully added to
 the new directory. This implies that in case of a crash or failure, the
 link to the child will not be lost, but it could be linked at both the old
 and new locations.

 The source link should not be the same as any link (directory and child name)
 in the ``to_dir`` path. This restriction is not enforced, but it may be
 enforced in a future version. If it were violated then the result would be
 to create a cycle in the directory structure that is not necessarily reachable
 from the root of the destination path (``$NEWDIRCAP``), which could result in
 data loss, as described in ticket `#943`_.

.. _`#943`: https://tahoe-lafs.org/trac/tahoe-lafs/ticket/943


Other Utilities
---------------

``GET /uri?uri=$CAP``

  This causes a redirect to /uri/$CAP, and retains any additional query
  arguments (like filename= or save=). This is for the convenience of web
  forms which allow the user to paste in a read- or write- cap (obtained
  through some out-of-band channel, like IM or email).

  Note that this form merely redirects to the specific file or directory
  indicated by the $CAP: unlike the GET /uri/$DIRCAP form, you cannot
  traverse to children by appending additional path segments to the URL.

``GET /uri/$DIRCAP/[SUBDIRS../]?t=rename-form&name=$CHILDNAME``

  This provides a useful facility to browser-based user interfaces. It
  returns a page containing a form targetting the "POST $DIRCAP t=rename"
  functionality described above, with the provided $CHILDNAME present in the
  'from_name' field of that form. I.e. this presents a form offering to
  rename $CHILDNAME, requesting the new name, and submitting POST rename.
  This same URL format can also be used with "move-form" with the expected
  results.

``GET /uri/$DIRCAP/[SUBDIRS../]CHILDNAME?t=uri``

 This returns the file- or directory- cap for the specified object.

``GET /uri/$DIRCAP/[SUBDIRS../]CHILDNAME?t=readonly-uri``

 This returns a read-only file- or directory- cap for the specified object.
 If the object is an immutable file, this will return the same value as
 t=uri.


Debugging and Testing Features
------------------------------

These URLs are less-likely to be helpful to the casual Tahoe user, and are
mainly intended for developers.

``POST $URL?t=check``

 This triggers the FileChecker to determine the current "health" of the
 given file or directory, by counting how many shares are available. The
 page that is returned will display the results. This can be used as a "show
 me detailed information about this file" page.

 If a verify=true argument is provided, the node will perform a more
 intensive check, downloading and verifying every single bit of every share.

 If an add-lease=true argument is provided, the node will also add (or
 renew) a lease to every share it encounters. Each lease will keep the share
 alive for a certain period of time (one month by default). Once the last
 lease expires or is explicitly cancelled, the storage server is allowed to
 delete the share.

 If an output=JSON argument is provided, the response will be
 machine-readable JSON instead of human-oriented HTML. The data is a
 dictionary with the following keys::

  storage-index: a base32-encoded string with the objects's storage index,
                 or an empty string for LIT files
  summary: a string, with a one-line summary of the stats of the file
  results: a dictionary that describes the state of the file. For LIT files,
           this dictionary has only the 'healthy' key, which will always be
           True. For distributed files, this dictionary has the following
           keys:
    count-happiness: the servers-of-happiness level of the file, as
                     defined in doc/specifications/servers-of-happiness.
    count-shares-good: the number of good shares that were found
    count-shares-needed: 'k', the number of shares required for recovery
    count-shares-expected: 'N', the number of total shares generated
    count-good-share-hosts: the number of distinct storage servers with
                            good shares. Note that a high value does not
                            necessarily imply good share distribution,
                            because some of these servers may only hold
                            duplicate shares.
    count-wrong-shares: for mutable files, the number of shares for
                        versions other than the 'best' one (highest
                        sequence number, highest roothash). These are
                        either old, or created by an uncoordinated or
                        not fully successful write.
    count-recoverable-versions: for mutable files, the number of
                                recoverable versions of the file. For
                                a healthy file, this will equal 1.
    count-unrecoverable-versions: for mutable files, the number of
                                  unrecoverable versions of the file.
                                  For a healthy file, this will be 0.
    count-corrupt-shares: the number of shares with integrity failures
    list-corrupt-shares: a list of "share locators", one for each share
                         that was found to be corrupt. Each share locator
                         is a list of (serverid, storage_index, sharenum).
    servers-responding: list of base32-encoded storage server identifiers,
                        one for each server which responded to the share
                        query.
    healthy: (bool) True if the file is completely healthy, False otherwise.
             Healthy files have at least N good shares. Overlapping shares
             do not currently cause a file to be marked unhealthy. If there
             are at least N good shares, then corrupt shares do not cause the
             file to be marked unhealthy, although the corrupt shares will be
             listed in the results (list-corrupt-shares) and should be manually
             removed to wasting time in subsequent downloads (as the
             downloader rediscovers the corruption and uses alternate shares).
             Future compatibility: the meaning of this field may change to
             reflect whether the servers-of-happiness criterion is met
             (see ticket #614).
    sharemap: dict mapping share identifier to list of serverids
              (base32-encoded strings). This indicates which servers are
              holding which shares. For immutable files, the shareid is
              an integer (the share number, from 0 to N-1). For
              immutable files, it is a string of the form
              'seq%d-%s-sh%d', containing the sequence number, the
              roothash, and the share number.

Before Tahoe-LAFS v1.11, the ``results`` dictionary also had a
``needs-rebalancing`` field, but that has been removed since it was computed
incorrectly.


``POST $URL?t=start-deep-check``    (must add &ophandle=XYZ)

 This initiates a recursive walk of all files and directories reachable from
 the target, performing a check on each one just like t=check. The result
 page will contain a summary of the results, including details on any
 file/directory that was not fully healthy.

 t=start-deep-check can only be invoked on a directory. An error (400
 BAD_REQUEST) will be signalled if it is invoked on a file. The recursive
 walker will deal with loops safely.

 This accepts the same verify= and add-lease= arguments as t=check.

 Since this operation can take a long time (perhaps a second per object),
 the ophandle= argument is required (see "Slow Operations, Progress, and
 Cancelling" above). The response to this POST will be a redirect to the
 corresponding /operations/$HANDLE page (with output=HTML or output=JSON to
 match the output= argument given to the POST). The deep-check operation
 will continue to run in the background, and the /operations page should be
 used to find out when the operation is done.

 Detailed check results for non-healthy files and directories will be
 available under /operations/$HANDLE/$STORAGEINDEX, and the HTML status will
 contain links to these detailed results.

 The HTML /operations/$HANDLE page for incomplete operations will contain a
 meta-refresh tag, set to 60 seconds, so that a browser which uses
 deep-check will automatically poll until the operation has completed.

 The JSON page (/options/$HANDLE?output=JSON) will contain a
 machine-readable JSON dictionary with the following keys::

  finished: a boolean, True if the operation is complete, else False. Some
            of the remaining keys may not be present until the operation
            is complete.
  root-storage-index: a base32-encoded string with the storage index of the
                      starting point of the deep-check operation
  count-objects-checked: count of how many objects were checked. Note that
                         non-distributed objects (i.e. small immutable LIT
                         files) are not checked, since for these objects,
                         the data is contained entirely in the URI.
  count-objects-healthy: how many of those objects were completely healthy
  count-objects-unhealthy: how many were damaged in some way
  count-corrupt-shares: how many shares were found to have corruption,
                        summed over all objects examined
  list-corrupt-shares: a list of "share identifiers", one for each share
                       that was found to be corrupt. Each share identifier
                       is a list of (serverid, storage_index, sharenum).
  list-unhealthy-files: a list of (pathname, check-results) tuples, for
                        each file that was not fully healthy. 'pathname' is
                        a list of strings (which can be joined by "/"
                        characters to turn it into a single string),
                        relative to the directory on which deep-check was
                        invoked. The 'check-results' field is the same as
                        that returned by t=check&output=JSON, described
                        above.
  stats: a dictionary with the same keys as the t=start-deep-stats command
         (described below)

``POST $URL?t=stream-deep-check``

 This initiates a recursive walk of all files and directories reachable from
 the target, performing a check on each one just like t=check. For each
 unique object (duplicates are skipped), a single line of JSON is emitted to
 the HTTP response channel (or an error indication, see below). When the walk
 is complete, a final line of JSON is emitted which contains the accumulated
 file-size/count "deep-stats" data.

 This command takes the same arguments as t=start-deep-check.

 A CLI tool can split the response stream on newlines into "response units",
 and parse each response unit as JSON. Each such parsed unit will be a
 dictionary, and will contain at least the "type" key: a string, one of
 "file", "directory", or "stats".

 For all units that have a type of "file" or "directory", the dictionary will
 contain the following keys::

  "path": a list of strings, with the path that is traversed to reach the
          object
  "cap": a write-cap URI for the file or directory, if available, else a
         read-cap URI
  "verifycap": a verify-cap URI for the file or directory
  "repaircap": an URI for the weakest cap that can still be used to repair
               the object
  "storage-index": a base32 storage index for the object
  "check-results": a copy of the dictionary which would be returned by
                   t=check&output=json, with three top-level keys:
                   "storage-index", "summary", and "results", and a variety
                   of counts and sharemaps in the "results" value.

 Note that non-distributed files (i.e. LIT files) will have values of None
 for verifycap, repaircap, and storage-index, since these files can neither
 be verified nor repaired, and are not stored on the storage servers.
 Likewise the check-results dictionary will be limited: an empty string for
 storage-index, and a results dictionary with only the "healthy" key.

 The last unit in the stream will have a type of "stats", and will contain
 the keys described in the "start-deep-stats" operation, below.

 If any errors occur during the traversal (specifically if a directory is
 unrecoverable, such that further traversal is not possible), an error
 indication is written to the response body, instead of the usual line of
 JSON. This error indication line will begin with the string "ERROR:" (in all
 caps), and contain a summary of the error on the rest of the line. The
 remaining lines of the response body will be a python exception. The client
 application should look for the ERROR: and stop processing JSON as soon as
 it is seen. Note that neither a file being unrecoverable nor a directory
 merely being unhealthy will cause traversal to stop. The line just before
 the ERROR: will describe the directory that was untraversable, since the
 unit is emitted to the HTTP response body before the child is traversed.


``POST $URL?t=check&repair=true``

 This performs a health check of the given file or directory, and if the
 checker determines that the object is not healthy (some shares are missing
 or corrupted), it will perform a "repair". During repair, any missing
 shares will be regenerated and uploaded to new servers.

 This accepts the same verify=true and add-lease= arguments as t=check. When
 an output=JSON argument is provided, the machine-readable JSON response
 will contain the following keys::

  storage-index: a base32-encoded string with the objects's storage index,
                 or an empty string for LIT files
  repair-attempted: (bool) True if repair was attempted
  repair-successful: (bool) True if repair was attempted and the file was
                     fully healthy afterwards. False if no repair was
                     attempted, or if a repair attempt failed.
  pre-repair-results: a dictionary that describes the state of the file
                      before any repair was performed. This contains exactly
                      the same keys as the 'results' value of the t=check
                      response, described above.
  post-repair-results: a dictionary that describes the state of the file
                       after any repair was performed. If no repair was
                       performed, post-repair-results and pre-repair-results
                       will be the same. This contains exactly the same keys
                       as the 'results' value of the t=check response,
                       described above.

``POST $URL?t=start-deep-check&repair=true``    (must add &ophandle=XYZ)

 This triggers a recursive walk of all files and directories, performing a
 t=check&repair=true on each one.

 Like t=start-deep-check without the repair= argument, this can only be
 invoked on a directory. An error (400 BAD_REQUEST) will be signalled if it
 is invoked on a file. The recursive walker will deal with loops safely.

 This accepts the same verify= and add-lease= arguments as
 t=start-deep-check. It uses the same ophandle= mechanism as
 start-deep-check. When an output=JSON argument is provided, the response
 will contain the following keys::

  finished: (bool) True if the operation has completed, else False
  root-storage-index: a base32-encoded string with the storage index of the
                      starting point of the deep-check operation
  count-objects-checked: count of how many objects were checked

  count-objects-healthy-pre-repair: how many of those objects were completely
                                    healthy, before any repair
  count-objects-unhealthy-pre-repair: how many were damaged in some way
  count-objects-healthy-post-repair: how many of those objects were completely
                                      healthy, after any repair
  count-objects-unhealthy-post-repair: how many were damaged in some way

  count-repairs-attempted: repairs were attempted on this many objects.
  count-repairs-successful: how many repairs resulted in healthy objects
  count-repairs-unsuccessful: how many repairs resulted did not results in
                              completely healthy objects
  count-corrupt-shares-pre-repair: how many shares were found to have
                                   corruption, summed over all objects
                                   examined, before any repair
  count-corrupt-shares-post-repair: how many shares were found to have
                                    corruption, summed over all objects
                                    examined, after any repair
  list-corrupt-shares: a list of "share identifiers", one for each share
                       that was found to be corrupt (before any repair).
                       Each share identifier is a list of (serverid,
                       storage_index, sharenum).
  list-remaining-corrupt-shares: like list-corrupt-shares, but mutable shares
                                 that were successfully repaired are not
                                 included. These are shares that need
                                 manual processing. Since immutable shares
                                 cannot be modified by clients, all corruption
                                 in immutable shares will be listed here.
  list-unhealthy-files: a list of (pathname, check-results) tuples, for
                        each file that was not fully healthy. 'pathname' is
                        relative to the directory on which deep-check was
                        invoked. The 'check-results' field is the same as
                        that returned by t=check&repair=true&output=JSON,
                        described above.
  stats: a dictionary with the same keys as the t=start-deep-stats command
         (described below)

``POST $URL?t=stream-deep-check&repair=true``

 This triggers a recursive walk of all files and directories, performing a
 t=check&repair=true on each one. For each unique object (duplicates are
 skipped), a single line of JSON is emitted to the HTTP response channel (or
 an error indication). When the walk is complete, a final line of JSON is
 emitted which contains the accumulated file-size/count "deep-stats" data.

 This emits the same data as t=stream-deep-check (without the repair=true),
 except that the "check-results" field is replaced with a
 "check-and-repair-results" field, which contains the keys returned by
 t=check&repair=true&output=json (i.e. repair-attempted, repair-successful,
 pre-repair-results, and post-repair-results). The output does not contain
 the summary dictionary that is provied by t=start-deep-check&repair=true
 (the one with count-objects-checked and list-unhealthy-files), since the
 receiving client is expected to calculate those values itself from the
 stream of per-object check-and-repair-results.

 Note that the "ERROR:" indication will only be emitted if traversal stops,
 which will only occur if an unrecoverable directory is encountered. If a
 file or directory repair fails, the traversal will continue, and the repair
 failure will be indicated in the JSON data (in the "repair-successful" key).

``POST $DIRURL?t=start-manifest``    (must add &ophandle=XYZ)

 This operation generates a "manfest" of the given directory tree, mostly
 for debugging. This is a table of (path, filecap/dircap), for every object
 reachable from the starting directory. The path will be slash-joined, and
 the filecap/dircap will contain a link to the object in question. This page
 gives immediate access to every object in the file store subtree.

 This operation uses the same ophandle= mechanism as deep-check. The
 corresponding /operations/$HANDLE page has three different forms. The
 default is output=HTML.

 If output=text is added to the query args, the results will be a text/plain
 list. The first line is special: it is either "finished: yes" or "finished:
 no"; if the operation is not finished, you must periodically reload the
 page until it completes. The rest of the results are a plaintext list, with
 one file/dir per line, slash-separated, with the filecap/dircap separated
 by a space.

 If output=JSON is added to the queryargs, then the results will be a
 JSON-formatted dictionary with six keys. Note that because large directory
 structures can result in very large JSON results, the full results will not
 be available until the operation is complete (i.e. until output["finished"]
 is True)::

  finished (bool): if False then you must reload the page until True
  origin_si (base32 str): the storage index of the starting point
  manifest: list of (path, cap) tuples, where path is a list of strings.
  verifycaps: list of (printable) verify cap strings
  storage-index: list of (base32) storage index strings
  stats: a dictionary with the same keys as the t=start-deep-stats command
         (described below)

``POST $DIRURL?t=start-deep-size``   (must add &ophandle=XYZ)

 This operation generates a number (in bytes) containing the sum of the
 filesize of all directories and immutable files reachable from the given
 directory. This is a rough lower bound of the total space consumed by this
 subtree. It does not include space consumed by mutable files, nor does it
 take expansion or encoding overhead into account. Later versions of the
 code may improve this estimate upwards.

 The /operations/$HANDLE status output consists of two lines of text::

  finished: yes
  size: 1234

``POST $DIRURL?t=start-deep-stats``    (must add &ophandle=XYZ)

 This operation performs a recursive walk of all files and directories
 reachable from the given directory, and generates a collection of
 statistics about those objects.

 The result (obtained from the /operations/$OPHANDLE page) is a
 JSON-serialized dictionary with the following keys (note that some of these
 keys may be missing until 'finished' is True)::

  finished: (bool) True if the operation has finished, else False
  api-version: (int), number of deep-stats API version. Will be increased every
               time backwards-incompatible change is introduced.
               Current version is 1.
  count-immutable-files: count of how many CHK files are in the set
  count-mutable-files: same, for mutable files (does not include directories)
  count-literal-files: same, for LIT files (data contained inside the URI)
  count-files: sum of the above three
  count-directories: count of directories
  count-unknown: count of unrecognized objects (perhaps from the future)
  size-immutable-files: total bytes for all CHK files in the set, =deep-size
  size-mutable-files (TODO): same, for current version of all mutable files
  size-literal-files: same, for LIT files
  size-directories: size of directories (includes size-literal-files)
  size-files-histogram: list of (minsize, maxsize, count) buckets,
                        with a histogram of filesizes, 5dB/bucket,
                        for both literal and immutable files
  largest-directory: number of children in the largest directory
  largest-immutable-file: number of bytes in the largest CHK file

 size-mutable-files is not implemented, because it would require extra
 queries to each mutable file to get their size. This may be implemented in
 the future.

 Assuming no sharing, the basic space consumed by a single root directory is
 the sum of size-immutable-files, size-mutable-files, and size-directories.
 The actual disk space used by the shares is larger, because of the
 following sources of overhead::

  integrity data
  expansion due to erasure coding
  share management data (leases)
  backend (ext3) minimum block size

``POST $URL?t=stream-manifest``

 This operation performs a recursive walk of all files and directories
 reachable from the given starting point. For each such unique object
 (duplicates are skipped), a single line of JSON is emitted to the HTTP
 response channel (or an error indication, see below). When the walk is
 complete, a final line of JSON is emitted which contains the accumulated
 file-size/count "deep-stats" data.

 A CLI tool can split the response stream on newlines into "response units",
 and parse each response unit as JSON. Each such parsed unit will be a
 dictionary, and will contain at least the "type" key: a string, one of
 "file", "directory", or "stats".

 For all units that have a type of "file" or "directory", the dictionary will
 contain the following keys::

  "path": a list of strings, with the path that is traversed to reach the
          object
  "cap": a write-cap URI for the file or directory, if available, else a
         read-cap URI
  "verifycap": a verify-cap URI for the file or directory
  "repaircap": an URI for the weakest cap that can still be used to repair
               the object
  "storage-index": a base32 storage index for the object

 Note that non-distributed files (i.e. LIT files) will have values of None
 for verifycap, repaircap, and storage-index, since these files can neither
 be verified nor repaired, and are not stored on the storage servers.

 The last unit in the stream will have a type of "stats", and will contain
 the keys described in the "start-deep-stats" operation, below.

 If any errors occur during the traversal (specifically if a directory is
 unrecoverable, such that further traversal is not possible), an error
 indication is written to the response body, instead of the usual line of
 JSON. This error indication line will begin with the string "ERROR:" (in all
 caps), and contain a summary of the error on the rest of the line. The
 remaining lines of the response body will be a python exception. The client
 application should look for the ERROR: and stop processing JSON as soon as
 it is seen. The line just before the ERROR: will describe the directory that
 was untraversable, since the manifest entry is emitted to the HTTP response
 body before the child is traversed.


Other Useful Pages
==================

The portion of the web namespace that begins with "/uri" (and "/named") is
dedicated to giving users (both humans and programs) access to the Tahoe-LAFS
file store. The rest of the namespace provides status information about the
state of the Tahoe-LAFS node.

``GET /``   (the root page)

This is the "Welcome Page", and contains a few distinct sections::

 Node information: library versions, local nodeid, services being provided.

 File store access forms: create a new directory, view a file/directory by
                          URI, upload a file (unlinked), download a file by
                          URI.

 Grid status: introducer information, helper information, connected storage
              servers.

``GET /?t=json``   (the json welcome page)

This is the "json Welcome Page", and contains connectivity status
of the introducer(s) and storage server(s), here's an example::

  {
   "introducers": {
    "statuses": []
   },
   "servers": [{
     "nodeid": "other_nodeid",
     "available_space": 123456,
     "nickname": "George \u263b",
     "version": "1.0",
     "connection_status": "summary",
     "last_received_data": 1487811257
    }]
  }


The above json ``introducers`` section includes a list of
introducer connectivity status messages.

The above json ``servers`` section is an array with map elements.  Each map
has the following properties:

1. ``nodeid`` - an identifier derived from the node's public key
2. ``available_space`` - the available space in bytes expressed as an integer
3. ``nickname`` - the storage server nickname
4. ``version`` - the storage server Tahoe-LAFS version
5. ``connection_status`` - connectivity status
6. ``last_received_data`` - the time when data was last received,
   expressed in seconds since epoch

``GET /status/``

 This page lists all active uploads and downloads, and contains a short list
 of recent upload/download operations. Each operation has a link to a page
 that describes file sizes, servers that were involved, and the time consumed
 in each phase of the operation.

 A GET of /status/?t=json will contain a machine-readable subset of the same
 data. It returns a JSON-encoded dictionary. The only key defined at this
 time is "active", with a value that is a list of operation dictionaries, one
 for each active operation. Once an operation is completed, it will no longer
 appear in data["active"] .

 Each op-dict contains a "type" key, one of "upload", "download",
 "mapupdate", "publish", or "retrieve" (the first two are for immutable
 files, while the latter three are for mutable files and directories).

 The "upload" op-dict will contain the following keys::

  type (string): "upload"
  storage-index-string (string): a base32-encoded storage index
  total-size (int): total size of the file
  status (string): current status of the operation
  progress-hash (float): 1.0 when the file has been hashed
  progress-ciphertext (float): 1.0 when the file has been encrypted.
  progress-encode-push (float): 1.0 when the file has been encoded and
                                pushed to the storage servers. For helper
                                uploads, the ciphertext value climbs to 1.0
                                first, then encoding starts. For unassisted
                                uploads, ciphertext and encode-push progress
                                will climb at the same pace.

 The "download" op-dict will contain the following keys::

  type (string): "download"
  storage-index-string (string): a base32-encoded storage index
  total-size (int): total size of the file
  status (string): current status of the operation
  progress (float): 1.0 when the file has been fully downloaded

 Front-ends which want to report progress information are advised to simply
 average together all the progress-* indicators. A slightly more accurate
 value can be found by ignoring the progress-hash value (since the current
 implementation hashes synchronously, so clients will probably never see
 progress-hash!=1.0).

``GET /helper_status/``

 If the node is running a helper (i.e. if [helper]enabled is set to True in
 tahoe.cfg), then this page will provide a list of all the helper operations
 currently in progress. If "?t=json" is added to the URL, it will return a
 JSON-formatted list of helper statistics, which can then be used to produce
 graphs to indicate how busy the helper is.

``GET /statistics/``

 This page provides "node statistics", which are collected from a variety of
 sources::

   load_monitor: every second, the node schedules a timer for one second in
                 the future, then measures how late the subsequent callback
                 is. The "load_average" is this tardiness, measured in
                 seconds, averaged over the last minute. It is an indication
                 of a busy node, one which is doing more work than can be
                 completed in a timely fashion. The "max_load" value is the
                 highest value that has been seen in the last 60 seconds.

   cpu_monitor: every minute, the node uses time.clock() to measure how much
                CPU time it has used, and it uses this value to produce
                1min/5min/15min moving averages. These values range from 0%
                (0.0) to 100% (1.0), and indicate what fraction of the CPU
                has been used by the Tahoe node. Not all operating systems
                provide meaningful data to time.clock(): they may report 100%
                CPU usage at all times.

   uploader: this counts how many immutable files (and bytes) have been
             uploaded since the node was started

   downloader: this counts how many immutable files have been downloaded
               since the node was started

   publishes: this counts how many mutable files (including directories) have
              been modified since the node was started

   retrieves: this counts how many mutable files (including directories) have
              been read since the node was started

 There are other statistics that are tracked by the node. The "raw stats"
 section shows a formatted dump of all of them.

 By adding "?t=json" to the URL, the node will return a JSON-formatted
 dictionary of stats values, which can be used by other tools to produce
 graphs of node behavior. The misc/munin/ directory in the source
 distribution provides some tools to produce these graphs.

``GET /``   (introducer status)

 For Introducer nodes, the welcome page displays information about both
 clients and servers which are connected to the introducer. Servers make
 "service announcements", and these are listed in a table. Clients will
 subscribe to hear about service announcements, and these subscriptions are
 listed in a separate table. Both tables contain information about what
 version of Tahoe is being run by the remote node, their advertised and
 outbound IP addresses, their nodeid and nickname, and how long they have
 been available.

 By adding "?t=json" to the URL, the node will return a JSON-formatted
 dictionary of stats values, which can be used to produce graphs of connected
 clients over time. This dictionary has the following keys::

  ["subscription_summary"] : a dictionary mapping service name (like
                             "storage") to an integer with the number of
                             clients that have subscribed to hear about that
                             service
  ["announcement_summary"] : a dictionary mapping service name to an integer
                             with the number of servers which are announcing
                             that service
  ["announcement_distinct_hosts"] : a dictionary mapping service name to an
                                    integer which represents the number of
                                    distinct hosts that are providing that
                                    service. If two servers have announced
                                    FURLs which use the same hostnames (but
                                    different ports and tubids), they are
                                    considered to be on the same host.


Static Files in /public_html
============================

The web-API server will take any request for a URL that starts with /static
and serve it from a configurable directory which defaults to
$BASEDIR/public_html . This is configured by setting the "[node]web.static"
value in $BASEDIR/tahoe.cfg . If this is left at the default value of
"public_html", then http://127.0.0.1:3456/static/subdir/foo.html will be
served with the contents of the file $BASEDIR/public_html/subdir/foo.html .

This can be useful to serve a javascript application which provides a
prettier front-end to the rest of the Tahoe web-API.


Safety and Security Issues -- Names vs. URIs
============================================

Summary: use explicit file- and dir- caps whenever possible, to reduce the
potential for surprises when the file store structure is changed.

Tahoe-LAFS provides a mutable file store, but the ways that the store can
change are limited. The only things that can change are:
 * the mapping from child names to child objects inside mutable directories
   (by adding a new child, removing an existing child, or changing an
   existing child to point to a different object)
 * the contents of mutable files

Obviously if you query for information about the file store and then act
to change it (such as by getting a listing of the contents of a mutable
directory and then adding a file to the directory), then the store might
have been changed after you queried it and before you acted upon it.
However, if you use the URI instead of the pathname of an object when you
act upon the object, then it will be the same object; only its contents
can change (if it is mutable). If, on the other hand, you act upon the
object using its pathname, then a different object might be in that place,
which can result in more kinds of surprises.

For example, suppose you are writing code which recursively downloads the
contents of a directory. The first thing your code does is fetch the listing
of the contents of the directory. For each child that it fetched, if that
child is a file then it downloads the file, and if that child is a directory
then it recurses into that directory. Now, if the download and the recurse
actions are performed using the child's name, then the results might be
wrong, because for example a child name that pointed to a subdirectory when
you listed the directory might have been changed to point to a file (in which
case your attempt to recurse into it would result in an error), or a child
name that pointed to a file when you listed the directory might now point to
a subdirectory (in which case your attempt to download the child would result
in a file containing HTML text describing the subdirectory!).

If your recursive algorithm uses the URI of the child instead of the name of
the child, then those kinds of mistakes just can't happen. Note that both the
child's name and the child's URI are included in the results of listing the
parent directory, so it isn't any harder to use the URI for this purpose.

The read and write caps in a given directory node are separate URIs, and
can't be assumed to point to the same object even if they were retrieved in
the same operation (although the web-API server attempts to ensure this
in most cases). If you need to rely on that property, you should explicitly
verify it. More generally, you should not make assumptions about the
internal consistency of the contents of mutable directories. As a result
of the signatures on mutable object versions, it is guaranteed that a given
version was written in a single update, but -- as in the case of a file --
the contents may have been chosen by a malicious writer in a way that is
designed to confuse applications that rely on their consistency.

In general, use names if you want "whatever object (whether file or
directory) is found by following this name (or sequence of names) when my
request reaches the server". Use URIs if you want "this particular object".


Concurrency Issues
==================

Tahoe uses both mutable and immutable files. Mutable files can be created
explicitly by doing an upload with ?mutable=true added, or implicitly by
creating a new directory (since a directory is just a special way to
interpret a given mutable file).

Mutable files suffer from the same consistency-vs-availability tradeoff that
all distributed data storage systems face. It is not possible to
simultaneously achieve perfect consistency and perfect availability in the
face of network partitions (servers being unreachable or faulty).

Tahoe tries to achieve a reasonable compromise, but there is a basic rule in
place, known as the Prime Coordination Directive: "Don't Do That". What this
means is that if write-access to a mutable file is available to several
parties, then those parties are responsible for coordinating their activities
to avoid multiple simultaneous updates. This could be achieved by having
these parties talk to each other and using some sort of locking mechanism, or
by serializing all changes through a single writer.

The consequences of performing uncoordinated writes can vary. Some of the
writers may lose their changes, as somebody else wins the race condition. In
many cases the file will be left in an "unhealthy" state, meaning that there
are not as many redundant shares as we would like (reducing the reliability
of the file against server failures). In the worst case, the file can be left
in such an unhealthy state that no version is recoverable, even the old ones.
It is this small possibility of data loss that prompts us to issue the Prime
Coordination Directive.

Tahoe nodes implement internal serialization to make sure that a single Tahoe
node cannot conflict with itself. For example, it is safe to issue two
directory modification requests to a single tahoe node's web-API server at the
same time, because the Tahoe node will internally delay one of them until
after the other has finished being applied. (This feature was introduced in
Tahoe-1.1; back with Tahoe-1.0 the web client was responsible for serializing
web requests themselves).

For more details, please see the "Consistency vs Availability" and "The Prime
Coordination Directive" sections of :doc:`../specifications/mutable`.


Access Blacklist
================

Gateway nodes may find it necessary to prohibit access to certain files. The
web-API has a facility to block access to filecaps by their storage index,
returning a 403 "Forbidden" error instead of the original file.

This blacklist is recorded in $NODEDIR/access.blacklist, and contains one
blocked file per line. Comment lines (starting with ``#``) are ignored. Each
line consists of the storage-index (in the usual base32 format as displayed
by the "More Info" page, or by the "tahoe debug dump-cap" command), followed
by whitespace, followed by a reason string, which will be included in the 403
error message. This could hold a URL to a page that explains why the file is
blocked, for example.

So for example, if you found a need to block access to a file with filecap
``URI:CHK:n7r3m6wmomelk4sep3kw5cvduq:os7ijw5c3maek7pg65e5254k2fzjflavtpejjyhshpsxuqzhcwwq:3:20:14861``,
you could do the following::

 tahoe debug dump-cap URI:CHK:n7r3m6wmomelk4sep3kw5cvduq:os7ijw5c3maek7pg65e5254k2fzjflavtpejjyhshpsxuqzhcwwq:3:20:14861
 -> storage index: whpepioyrnff7orecjolvbudeu
 echo "whpepioyrnff7orecjolvbudeu my puppy told me to" >>$NODEDIR/access.blacklist
 tahoe restart $NODEDIR
 tahoe get URI:CHK:n7r3m6wmomelk4sep3kw5cvduq:os7ijw5c3maek7pg65e5254k2fzjflavtpejjyhshpsxuqzhcwwq:3:20:14861
 -> error, 403 Access Prohibited: my puppy told me to

The ``access.blacklist`` file will be checked each time a file or directory
is accessed: the file's ``mtime`` is used to decide whether it need to be
reloaded. Therefore no node restart is necessary when creating the initial
blacklist, nor when adding second, third, or additional entries to the list.
When modifying the file, be careful to update it atomically, otherwise a
request may arrive while the file is only halfway written, and the partial
file may be incorrectly parsed.

The blacklist is applied to all access paths (including SFTP, FTP, and CLI
operations), not just the web-API. The blacklist also applies to directories.
If a directory is blacklisted, the gateway will refuse access to both that
directory and any child files/directories underneath it, when accessed via
"DIRCAP/SUBDIR/FILENAME" -style URLs. Users who go directly to the child
file/dir will bypass the blacklist.

The node will log the SI of the file being blocked, and the reason code, into
the ``logs/twistd.log`` file.

URLs and HTTP and UTF-8
=======================

.. _urls-and-utf8:

 HTTP does not provide a mechanism to specify the character set used to
 encode non-ASCII names in URLs (`RFC3986#2.1`_).  We prefer the convention
 that the ``filename=`` argument shall be a URL-escaped UTF-8 encoded Unicode
 string.  For example, suppose we want to provoke the server into using a
 filename of "f i a n c e-acute e" (i.e. f i a n c U+00E9 e). The UTF-8
 encoding of this is 0x66 0x69 0x61 0x6e 0x63 0xc3 0xa9 0x65 (or
 "fianc\\xC3\\xA9e", as python's ``repr()`` function would show). To encode
 this into a URL, the non-printable characters must be escaped with the
 urlencode ``%XX`` mechanism, giving us "fianc%C3%A9e". Thus, the first line
 of the HTTP request will be "``GET
 /uri/CAP...?save=true&filename=fianc%C3%A9e HTTP/1.1``". Not all browsers
 provide this: IE7 by default uses the Latin-1 encoding, which is "fianc%E9e"
 (although it has a configuration option to send URLs as UTF-8).

 The response header will need to indicate a non-ASCII filename. The actual
 mechanism to do this is not clear. For ASCII filenames, the response header
 would look like::

  Content-Disposition: attachment; filename="english.txt"

 If Tahoe were to enforce the UTF-8 convention, it would need to decode the
 URL argument into a Unicode string, and then encode it back into a sequence
 of bytes when creating the response header. One possibility would be to use
 unencoded UTF-8. Developers suggest that IE7 might accept this::

  #1: Content-Disposition: attachment; filename="fianc\xC3\xA9e"
    (note, the last four bytes of that line, not including the newline, are
    0xC3 0xA9 0x65 0x22)

 `RFC2231#4`_ (dated 1997): suggests that the following might work, and `some
 developers have reported`_ that it is supported by Firefox (but not IE7)::

  #2: Content-Disposition: attachment; filename*=utf-8''fianc%C3%A9e

 My reading of `RFC2616#19.5.1`_ (which defines Content-Disposition) says
 that the filename= parameter is defined to be wrapped in quotes (presumably
 to allow spaces without breaking the parsing of subsequent parameters),
 which would give us::

  #3: Content-Disposition: attachment; filename*=utf-8''"fianc%C3%A9e"

 However this is contrary to the examples in the email thread listed above.

 Developers report that IE7 (when it is configured for UTF-8 URL encoding,
 which is not the default in Asian countries), will accept::

  #4: Content-Disposition: attachment; filename=fianc%C3%A9e

 However, for maximum compatibility, Tahoe simply copies bytes from the URL
 into the response header, rather than enforcing the UTF-8 convention. This
 means it does not try to decode the filename from the URL argument, nor does
 it encode the filename into the response header.

.. _RFC3986#2.1: https://tools.ietf.org/html/rfc3986#section-2.1
.. _RFC2231#4: https://tools.ietf.org/html/rfc2231#section-4
.. _some developers have reported: http://markmail.org/message/dsjyokgl7hv64ig3
.. _RFC2616#19.5.1: https://tools.ietf.org/html/rfc2616#section-19.5.1
