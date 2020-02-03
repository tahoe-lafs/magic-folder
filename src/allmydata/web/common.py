
import time
import json

from twisted.web import http, server, resource, template
from twisted.python import log
from twisted.python.failure import Failure
from nevow import loaders, appserver
from nevow.rend import Page
from nevow.inevow import IRequest
from nevow.util import resource_filename
from allmydata import blacklist
from allmydata.interfaces import ExistingChildError, NoSuchChildError, \
     FileTooLargeError, NotEnoughSharesError, NoSharesError, \
     EmptyPathnameComponentError, MustBeDeepImmutableError, \
     MustBeReadonlyError, MustNotBeUnknownRWError, SDMF_VERSION, MDMF_VERSION
from allmydata.mutable.common import UnrecoverableFileError
from allmydata.util import abbreviate
from allmydata.util.hashutil import timing_safe_compare
from allmydata.util.time_format import format_time, format_delta
from allmydata.util.encodingutil import to_str, quote_output


def get_filenode_metadata(filenode):
    metadata = {'mutable': filenode.is_mutable()}
    if metadata['mutable']:
        mutable_type = filenode.get_version()
        assert mutable_type in (SDMF_VERSION, MDMF_VERSION)
        if mutable_type == MDMF_VERSION:
            file_format = "MDMF"
        else:
            file_format = "SDMF"
    else:
        file_format = "CHK"
    metadata['format'] = file_format
    size = filenode.get_size()
    if size is not None:
        metadata['size'] = size
    return metadata

def getxmlfile(name):
    return loaders.xmlfile(resource_filename('allmydata.web', '%s' % name))

def boolean_of_arg(arg):
    # TODO: ""
    if arg.lower() not in ("true", "t", "1", "false", "f", "0", "on", "off"):
        raise WebError("invalid boolean argument: %r" % (arg,), http.BAD_REQUEST)
    return arg.lower() in ("true", "t", "1", "on")

def parse_replace_arg(replace):
    if replace.lower() == "only-files":
        return replace
    try:
        return boolean_of_arg(replace)
    except WebError:
        raise WebError("invalid replace= argument: %r" % (replace,), http.BAD_REQUEST)


def get_format(req, default="CHK"):
    arg = get_arg(req, "format", None)
    if not arg:
        if boolean_of_arg(get_arg(req, "mutable", "false")):
            return "SDMF"
        return default
    if arg.upper() == "CHK":
        return "CHK"
    elif arg.upper() == "SDMF":
        return "SDMF"
    elif arg.upper() == "MDMF":
        return "MDMF"
    else:
        raise WebError("Unknown format: %s, I know CHK, SDMF, MDMF" % arg,
                       http.BAD_REQUEST)

def get_mutable_type(file_format): # accepts result of get_format()
    if file_format == "SDMF":
        return SDMF_VERSION
    elif file_format == "MDMF":
        return MDMF_VERSION
    else:
        # this is also used to identify which formats are mutable. Use
        #  if get_mutable_type(file_format) is not None:
        #      do_mutable()
        #  else:
        #      do_immutable()
        return None


def parse_offset_arg(offset):
    # XXX: This will raise a ValueError when invoked on something that
    # is not an integer. Is that okay? Or do we want a better error
    # message? Since this call is going to be used by programmers and
    # their tools rather than users (through the wui), it is not
    # inconsistent to return that, I guess.
    if offset is not None:
        offset = int(offset)

    return offset


def get_root(ctx_or_req):
    req = IRequest(ctx_or_req)
    # the addSlash=True gives us one extra (empty) segment
    depth = len(req.prepath) + len(req.postpath) - 1
    link = "/".join([".."] * depth)
    return link

def get_arg(ctx_or_req, argname, default=None, multiple=False):
    """Extract an argument from either the query args (req.args) or the form
    body fields (req.fields). If multiple=False, this returns a single value
    (or the default, which defaults to None), and the query args take
    precedence. If multiple=True, this returns a tuple of arguments (possibly
    empty), starting with all those in the query args.
    """
    req = IRequest(ctx_or_req)
    results = []
    if argname in req.args:
        results.extend(req.args[argname])
    if req.fields and argname in req.fields:
        results.append(req.fields[argname].value)
    if multiple:
        return tuple(results)
    if results:
        return results[0]
    return default

def convert_children_json(nodemaker, children_json):
    """I convert the JSON output of GET?t=json into the dict-of-nodes input
    to both dirnode.create_subdirectory() and
    client.create_directory(initial_children=). This is used by
    t=mkdir-with-children and t=mkdir-immutable"""
    children = {}
    if children_json:
        data = json.loads(children_json)
        for (namex, (ctype, propdict)) in data.iteritems():
            namex = unicode(namex)
            writecap = to_str(propdict.get("rw_uri"))
            readcap = to_str(propdict.get("ro_uri"))
            metadata = propdict.get("metadata", {})
            # name= argument is just for error reporting
            childnode = nodemaker.create_from_cap(writecap, readcap, name=namex)
            children[namex] = (childnode, metadata)
    return children

def abbreviate_time(data):
    # 1.23s, 790ms, 132us
    if data is None:
        return ""
    s = float(data)
    if s >= 10:
        return abbreviate.abbreviate_time(data)
    if s >= 1.0:
        return "%.2fs" % s
    if s >= 0.01:
        return "%.0fms" % (1000*s)
    if s >= 0.001:
        return "%.1fms" % (1000*s)
    return "%.0fus" % (1000000*s)

def compute_rate(bytes, seconds):
    if bytes is None:
      return None

    if seconds is None or seconds == 0:
      return None

    # negative values don't make sense here
    assert bytes > -1
    assert seconds > 0

    return 1.0 * bytes / seconds

def abbreviate_rate(data):
    # 21.8kBps, 554.4kBps 4.37MBps
    if data is None:
        return ""
    r = float(data)
    if r > 1000000:
        return "%1.2fMBps" % (r/1000000)
    if r > 1000:
        return "%.1fkBps" % (r/1000)
    return "%.0fBps" % r

def abbreviate_size(data):
    # 21.8kB, 554.4kB 4.37MB
    if data is None:
        return ""
    r = float(data)
    if r > 1000000000:
        return "%1.2fGB" % (r/1000000000)
    if r > 1000000:
        return "%1.2fMB" % (r/1000000)
    if r > 1000:
        return "%.1fkB" % (r/1000)
    return "%.0fB" % r

def plural(sequence_or_length):
    if isinstance(sequence_or_length, int):
        length = sequence_or_length
    else:
        length = len(sequence_or_length)
    if length == 1:
        return ""
    return "s"

def text_plain(text, ctx):
    req = IRequest(ctx)
    req.setHeader("content-type", "text/plain")
    req.setHeader("content-length", b"%d" % len(text))
    return text

def spaces_to_nbsp(text):
    return unicode(text).replace(u' ', u'\u00A0')

def render_time_delta(time_1, time_2):
    return spaces_to_nbsp(format_delta(time_1, time_2))

def render_time(t):
    return spaces_to_nbsp(format_time(time.localtime(t)))

def render_time_attr(t):
    return format_time(time.localtime(t))

class WebError(Exception):
    def __init__(self, text, code=http.BAD_REQUEST):
        self.text = text
        self.code = code

# XXX: to make UnsupportedMethod return 501 NOT_IMPLEMENTED instead of 500
# Internal Server Error, we either need to do that ICanHandleException trick,
# or make sure that childFactory returns a WebErrorResource (and never an
# actual exception). The latter is growing increasingly annoying.

def should_create_intermediate_directories(req):
    t = get_arg(req, "t", "").strip()
    return bool(req.method in ("PUT", "POST") and
                t not in ("delete", "rename", "rename-form", "check"))

def humanize_failure(f):
    # return text, responsecode
    if f.check(EmptyPathnameComponentError):
        return ("The webapi does not allow empty pathname components, "
                "i.e. a double slash", http.BAD_REQUEST)
    if f.check(ExistingChildError):
        return ("There was already a child by that name, and you asked me "
                "to not replace it.", http.CONFLICT)
    if f.check(NoSuchChildError):
        quoted_name = quote_output(f.value.args[0], encoding="utf-8", quotemarks=False)
        return ("No such child: %s" % quoted_name, http.NOT_FOUND)
    if f.check(NotEnoughSharesError):
        t = ("NotEnoughSharesError: This indicates that some "
             "servers were unavailable, or that shares have been "
             "lost to server departure, hard drive failure, or disk "
             "corruption. You should perform a filecheck on "
             "this object to learn more.\n\nThe full error message is:\n"
             "%s") % str(f.value)
        return (t, http.GONE)
    if f.check(NoSharesError):
        t = ("NoSharesError: no shares could be found. "
             "Zero shares usually indicates a corrupt URI, or that "
             "no servers were connected, but it might also indicate "
             "severe corruption. You should perform a filecheck on "
             "this object to learn more.\n\nThe full error message is:\n"
             "%s") % str(f.value)
        return (t, http.GONE)
    if f.check(UnrecoverableFileError):
        t = ("UnrecoverableFileError: the directory (or mutable file) could "
             "not be retrieved, because there were insufficient good shares. "
             "This might indicate that no servers were connected, "
             "insufficient servers were connected, the URI was corrupt, or "
             "that shares have been lost due to server departure, hard drive "
             "failure, or disk corruption. You should perform a filecheck on "
             "this object to learn more.")
        return (t, http.GONE)
    if f.check(MustNotBeUnknownRWError):
        quoted_name = quote_output(f.value.args[1], encoding="utf-8")
        immutable = f.value.args[2]
        if immutable:
            t = ("MustNotBeUnknownRWError: an operation to add a child named "
                 "%s to a directory was given an unknown cap in a write slot.\n"
                 "If the cap is actually an immutable readcap, then using a "
                 "webapi server that supports a later version of Tahoe may help.\n\n"
                 "If you are using the webapi directly, then specifying an immutable "
                 "readcap in the read slot (ro_uri) of the JSON PROPDICT, and "
                 "omitting the write slot (rw_uri), would also work in this "
                 "case.") % quoted_name
        else:
            t = ("MustNotBeUnknownRWError: an operation to add a child named "
                 "%s to a directory was given an unknown cap in a write slot.\n"
                 "Using a webapi server that supports a later version of Tahoe "
                 "may help.\n\n"
                 "If you are using the webapi directly, specifying a readcap in "
                 "the read slot (ro_uri) of the JSON PROPDICT, as well as a "
                 "writecap in the write slot if desired, would also work in this "
                 "case.") % quoted_name
        return (t, http.BAD_REQUEST)
    if f.check(MustBeDeepImmutableError):
        quoted_name = quote_output(f.value.args[1], encoding="utf-8")
        t = ("MustBeDeepImmutableError: a cap passed to this operation for "
             "the child named %s, needed to be immutable but was not. Either "
             "the cap is being added to an immutable directory, or it was "
             "originally retrieved from an immutable directory as an unknown "
             "cap.") % quoted_name
        return (t, http.BAD_REQUEST)
    if f.check(MustBeReadonlyError):
        quoted_name = quote_output(f.value.args[1], encoding="utf-8")
        t = ("MustBeReadonlyError: a cap passed to this operation for "
             "the child named '%s', needed to be read-only but was not. "
             "The cap is being passed in a read slot (ro_uri), or was retrieved "
             "from a read slot as an unknown cap.") % quoted_name
        return (t, http.BAD_REQUEST)
    if f.check(blacklist.FileProhibited):
        t = "Access Prohibited: %s" % quote_output(f.value.reason, encoding="utf-8", quotemarks=False)
        return (t, http.FORBIDDEN)
    if f.check(WebError):
        return (f.value.text, f.value.code)
    if f.check(FileTooLargeError):
        return (f.getTraceback(), http.REQUEST_ENTITY_TOO_LARGE)
    return (str(f), None)

class MyExceptionHandler(appserver.DefaultExceptionHandler, object):
    def simple(self, ctx, text, code=http.BAD_REQUEST):
        req = IRequest(ctx)
        req.setResponseCode(code)
        #req.responseHeaders.setRawHeaders("content-encoding", [])
        #req.responseHeaders.setRawHeaders("content-disposition", [])
        req.setHeader("content-type", "text/plain;charset=utf-8")
        if isinstance(text, unicode):
            text = text.encode("utf-8")
        req.setHeader("content-length", b"%d" % len(text))
        req.write(text)
        # TODO: consider putting the requested URL here
        req.finishRequest(False)

    def renderHTTP_exception(self, ctx, f):
        try:
            text, code = humanize_failure(f)
        except:
            log.msg("exception in humanize_failure")
            log.msg("argument was %s" % (f,))
            log.err()
            text, code = str(f), None
        if code is not None:
            return self.simple(ctx, text, code)
        if f.check(server.UnsupportedMethod):
            # twisted.web.server.Request.render() has support for transforming
            # this into an appropriate 501 NOT_IMPLEMENTED or 405 NOT_ALLOWED
            # return code, but nevow does not.
            req = IRequest(ctx)
            method = req.method
            return self.simple(ctx,
                               "I don't know how to treat a %s request." % method,
                               http.NOT_IMPLEMENTED)
        req = IRequest(ctx)
        accept = req.getHeader("accept")
        if not accept:
            accept = "*/*"
        if "*/*" in accept or "text/*" in accept or "text/html" in accept:
            super = appserver.DefaultExceptionHandler
            return super.renderHTTP_exception(self, ctx, f)
        # use plain text
        traceback = f.getTraceback()
        return self.simple(ctx, traceback, http.INTERNAL_SERVER_ERROR)


class NeedOperationHandleError(WebError):
    pass


class RenderMixin(object):

    def renderHTTP(self, ctx):
        request = IRequest(ctx)

        # if we were using regular twisted.web Resources (and the regular
        # twisted.web.server.Request object) then we could implement
        # render_PUT and render_GET. But Nevow's request handler
        # (NevowRequest.gotPageContext) goes directly to renderHTTP. Copy
        # some code from the Resource.render method that Nevow bypasses, to
        # do the same thing.
        m = getattr(self, 'render_' + request.method, None)
        if not m:
            raise server.UnsupportedMethod(getattr(self, 'allowedMethods', ()))
        return m(ctx)

    def render_OPTIONS(self, ctx):
        """
        Handle HTTP OPTIONS request by adding appropriate headers.

        :param ctx: client transaction from which request is extracted
        :returns: str (empty)
        """
        req = IRequest(ctx)
        req.setHeader("server", "Tahoe-LAFS gateway")
        methods = ', '.join([m[7:] for m in dir(self) if m.startswith('render_')])
        req.setHeader("allow", methods)
        req.setHeader("public", methods)
        req.setHeader("compliance", "rfc=2068, rfc=2616")
        req.setHeader("content-length", 0)
        return ""


class MultiFormatPage(Page):
    """
    ```MultiFormatPage`` is a ``rend.Page`` that can be rendered in a number
    of different formats.

    Rendered format is controlled by a query argument (given by
    ``self.formatArgument``).  Different resources may support different
    formats but ``json`` is a pretty common one.
    """
    formatArgument = "t"
    formatDefault = None

    def renderHTTP(self, ctx):
        """
        Dispatch to a renderer for a particular format, as selected by a query
        argument.

        A renderer for the format given by the query argument matching
        ``formatArgument`` will be selected and invoked.  The default ``Page``
        rendering behavior will be used if no format is selected (either by
        query arguments or by ``formatDefault``).

        :return: The result of the selected renderer.
        """
        req = IRequest(ctx)
        t = get_arg(req, self.formatArgument, self.formatDefault)
        renderer = self._get_renderer(t)
        result = renderer(ctx)
        return result


    def _get_renderer(self, fmt):
        """
        Get the renderer for the indicated format.

        :param bytes fmt: The format.  If a method with a prefix of
            ``render_`` and a suffix of this format (upper-cased) is found, it
            will be used.

        :return: A callable which takes a Nevow context and renders a
            response.
        """
        if fmt is None:
            return super(MultiFormatPage, self).renderHTTP
        try:
            renderer = getattr(self, "render_{}".format(fmt.upper()))
        except AttributeError:
            raise WebError(
                "Unknown {} value: {!r}".format(self.formatArgument, fmt),
            )
        else:
            if renderer is None:
                return super(MultiFormatPage, self).renderHTTP
            return lambda ctx: renderer(IRequest(ctx))


class MultiFormatResource(resource.Resource, object):
    """
    ``MultiFormatResource`` is a ``resource.Resource`` that can be rendered in
    a number of different formats.

    Rendered format is controlled by a query argument (given by
    ``self.formatArgument``).  Different resources may support different
    formats but ``json`` is a pretty common one.  ``html`` is the default
    format if nothing else is given as the ``formatDefault``.
    """
    formatArgument = "t"
    formatDefault = None

    def render(self, req):
        """
        Dispatch to a renderer for a particular format, as selected by a query
        argument.

        A renderer for the format given by the query argument matching
        ``formatArgument`` will be selected and invoked.  render_HTML will be
        used as a default if no format is selected (either by query arguments
        or by ``formatDefault``).

        :return: The result of the selected renderer.
        """
        t = get_arg(req, self.formatArgument, self.formatDefault)
        renderer = self._get_renderer(t)
        return renderer(req)

    def _get_renderer(self, fmt):
        """
        Get the renderer for the indicated format.

        :param str fmt: The format.  If a method with a prefix of ``render_``
            and a suffix of this format (upper-cased) is found, it will be
            used.

        :return: A callable which takes a twisted.web Request and renders a
            response.
        """
        renderer = None

        if fmt is not None:
            try:
                renderer = getattr(self, "render_{}".format(fmt.upper()))
            except AttributeError:
                raise WebError(
                    "Unknown {} value: {!r}".format(self.formatArgument, fmt),
                )

        if renderer is None:
            renderer = self.render_HTML

        return renderer


class SlotsSequenceElement(template.Element):
    """
    ``SlotsSequenceElement` is a minimal port of nevow's sequence renderer for
    twisted.web.template.

    Tags passed in to be templated will have two renderers available: ``item``
    and ``tag``.
    """

    def __init__(self, tag, seq):
        self.loader = template.TagLoader(tag)
        self.seq = seq

    @template.renderer
    def item(self, request, tag):
        """
        A template renderer for each sequence item.

        ``tag`` will be cloned for each item in the sequence provided, and its
        slots filled from the sequence item. Each item must be dict-like enough
        for ``tag.fillSlots(**item)``. Each cloned tag will be siblings with no
        separator beween them.
        """
        for item in self.seq:
            yield tag.clone(deep=False).fillSlots(**item)

    @template.renderer
    def empty(self, request, tag):
        """
        A template renderer for empty sequences.

        This renderer will either return ``tag`` unmodified if the provided
        sequence has no items, or return the empty string if there are any
        items.
        """
        if len(self.seq) > 0:
            return u''
        else:
            return tag


class TokenOnlyWebApi(resource.Resource, object):
    """
    I provide a rend.Page implementation that only accepts POST calls,
    and only if they have a 'token=' arg with the correct
    authentication token (see
    :meth:`allmydata.client.Client.get_auth_token`). Callers must also
    provide the "t=" argument to indicate the return-value (the only
    valid value for this is "json")

    Subclasses should override 'post_json' which should process the
    API call and return a string which encodes a valid JSON
    object. This will only be called if the correct token is present
    and valid (during renderHTTP processing).
    """

    def __init__(self, client):
        self.client = client

    def post_json(self, req):
        return NotImplemented

    def render(self, req):
        if req.method != 'POST':
            raise server.UnsupportedMethod(('POST',))
        if req.args.get('token', False):
            raise WebError("Do not pass 'token' as URL argument", http.BAD_REQUEST)
        # not using get_arg() here because we *don't* want the token
        # argument to work if you passed it as a GET-style argument
        token = None
        if req.fields and 'token' in req.fields:
            token = req.fields['token'].value.strip()
        if not token:
            raise WebError("Missing token", http.UNAUTHORIZED)
        if not timing_safe_compare(token, self.client.get_auth_token()):
            raise WebError("Invalid token", http.UNAUTHORIZED)

        t = get_arg(req, "t", "").strip()
        if not t:
            raise WebError("Must provide 't=' argument")
        if t == u'json':
            try:
                return self.post_json(req)
            except WebError as e:
                req.setResponseCode(e.code)
                return json.dumps({"error": e.text})
            except Exception as e:
                message, code = humanize_failure(Failure())
                req.setResponseCode(500 if code is None else code)
                return json.dumps({"error": message})
        else:
            raise WebError("'%s' invalid type for 't' arg" % (t,), http.BAD_REQUEST)
