import os
import time
import json
import urllib

from twisted.web import (
    http,
    resource,
)
from twisted.web.util import redirectTo

from hyperlink import URL

from nevow import rend, tags as T
from nevow.inevow import IRequest
from nevow.static import File as nevow_File # TODO: merge with static.File?
from nevow.util import resource_filename

import allmydata # to display import path
from allmydata.version_checks import get_package_versions_string
from allmydata.util import log
from allmydata.interfaces import IFileNode
from allmydata.web import filenode, directory, unlinked, status
from allmydata.web import storage, magic_folder
from allmydata.web.common import (
    abbreviate_size,
    getxmlfile,
    WebError,
    get_arg,
    MultiFormatPage,
    RenderMixin,
    get_format,
    get_mutable_type,
    render_time_delta,
    render_time,
    render_time_attr,
)
from allmydata.web.private import (
    create_private_tree,
)
from allmydata import uri

class URIHandler(resource.Resource, object):
    """
    I live at /uri . There are several operations defined on /uri itself,
    mostly involved with creation of unlinked files and directories.
    """

    def __init__(self, client):
        super(URIHandler, self).__init__()
        self.client = client

    def render_GET(self, req):
        """
        Historically, accessing this via "GET /uri?uri=<capabilitiy>"
        was/is a feature -- which simply redirects to the more-common
        "GET /uri/<capability>" with any other query args
        preserved. New code should use "/uri/<cap>"
        """
        uri_arg = req.args.get(b"uri", [None])[0]
        if uri_arg is None:
            raise WebError("GET /uri requires uri=")

        # shennanigans like putting "%2F" or just "/" itself, or ../
        # etc in the <cap> might be a vector for weirdness so we
        # validate that this is a valid capability before proceeding.
        cap = uri.from_string(uri_arg)
        if isinstance(cap, uri.UnknownURI):
            raise WebError("Invalid capability")

        # so, using URL.from_text(req.uri) isn't going to work because
        # it seems Nevow was creating absolute URLs including
        # host/port whereas req.uri is absolute (but lacks host/port)
        redir_uri = URL.from_text(req.prePathURL().decode('utf8'))
        redir_uri = redir_uri.child(urllib.quote(uri_arg).decode('utf8'))
        # add back all the query args that AREN'T "?uri="
        for k, values in req.args.items():
            if k != b"uri":
                for v in values:
                    redir_uri = redir_uri.add(k.decode('utf8'), v.decode('utf8'))
        return redirectTo(redir_uri.to_text().encode('utf8'), req)

    def render_PUT(self, req):
        """
        either "PUT /uri" to create an unlinked file, or
        "PUT /uri?t=mkdir" to create an unlinked directory
        """
        t = get_arg(req, "t", "").strip()
        if t == "":
            file_format = get_format(req, "CHK")
            mutable_type = get_mutable_type(file_format)
            if mutable_type is not None:
                return unlinked.PUTUnlinkedSSK(req, self.client, mutable_type)
            else:
                return unlinked.PUTUnlinkedCHK(req, self.client)
        if t == "mkdir":
            return unlinked.PUTUnlinkedCreateDirectory(req, self.client)
        errmsg = (
            "/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, "
            "and POST?t=mkdir"
        )
        raise WebError(errmsg, http.BAD_REQUEST)

    def render_POST(self, req):
        """
        "POST /uri?t=upload&file=newfile" to upload an
        unlinked file or "POST /uri?t=mkdir" to create a
        new directory
        """
        t = get_arg(req, "t", "").strip()
        if t in ("", "upload"):
            file_format = get_format(req)
            mutable_type = get_mutable_type(file_format)
            if mutable_type is not None:
                return unlinked.POSTUnlinkedSSK(req, self.client, mutable_type)
            else:
                return unlinked.POSTUnlinkedCHK(req, self.client)
        if t == "mkdir":
            return unlinked.POSTUnlinkedCreateDirectory(req, self.client)
        elif t == "mkdir-with-children":
            return unlinked.POSTUnlinkedCreateDirectoryWithChildren(req,
                                                                    self.client)
        elif t == "mkdir-immutable":
            return unlinked.POSTUnlinkedCreateImmutableDirectory(req,
                                                                 self.client)
        errmsg = ("/uri accepts only PUT, PUT?t=mkdir, POST?t=upload, "
                  "and POST?t=mkdir")
        raise WebError(errmsg, http.BAD_REQUEST)

    def getChild(self, name, req):
        """
        Most requests look like /uri/<cap> so this fetches the capability
        and creates and appropriate handler (depending on the kind of
        capability it was passed).
        """
        try:
            node = self.client.create_node_from_uri(name)
            return directory.make_handler_for(node, self.client)
        except (TypeError, AssertionError):
            raise WebError(
                "'{}' is not a valid file- or directory- cap".format(name)
            )


class FileHandler(rend.Page):
    # I handle /file/$FILECAP[/IGNORED] , which provides a URL from which a
    # file can be downloaded correctly by tools like "wget".

    def __init__(self, client):
        rend.Page.__init__(self, client)
        self.client = client

    def childFactory(self, ctx, name):
        req = IRequest(ctx)
        if req.method not in ("GET", "HEAD"):
            raise WebError("/file can only be used with GET or HEAD")
        # 'name' must be a file URI
        try:
            node = self.client.create_node_from_uri(name)
        except (TypeError, AssertionError):
            # I think this can no longer be reached
            raise WebError("'%s' is not a valid file- or directory- cap"
                           % name)
        if not IFileNode.providedBy(node):
            raise WebError("'%s' is not a file-cap" % name)
        return filenode.FileNodeDownloadHandler(self.client, node)

    def renderHTTP(self, ctx):
        raise WebError("/file must be followed by a file-cap and a name",
                       http.NOT_FOUND)

class IncidentReporter(RenderMixin, rend.Page):
    def render_POST(self, ctx):
        req = IRequest(ctx)
        log.msg(format="User reports incident through web page: %(details)s",
                details=get_arg(req, "details", ""),
                level=log.WEIRD, umid="LkD9Pw")
        req.setHeader("content-type", "text/plain")
        return "An incident report has been saved to logs/incidents/ in the node directory."

SPACE = u"\u00A0"*2


class Root(MultiFormatPage):

    addSlash = True
    docFactory = getxmlfile("welcome.xhtml")

    _connectedalts = {
        "not-configured": "Not Configured",
        "yes": "Connected",
        "no": "Disconnected",
        }

    def __init__(self, client, clock=None, now_fn=None):
        rend.Page.__init__(self, client)
        self.client = client
        self.now_fn = now_fn

        self.putChild("uri", URIHandler(client))
        self.putChild("cap", URIHandler(client))

        # handler for "/magic_folder" URIs
        self.putChild("magic_folder", magic_folder.MagicFolderWebApi(client))

        # Handler for everything beneath "/private", an area of the resource
        # hierarchy which is only accessible with the private per-node API
        # auth token.
        self.putChild("private", create_private_tree(client.get_auth_token))

        self.putChild("file", FileHandler(client))
        self.putChild("named", FileHandler(client))
        self.putChild("status", status.Status(client.get_history()))
        self.putChild("statistics", status.Statistics(client.stats_provider))
        static_dir = resource_filename("allmydata.web", "static")
        for filen in os.listdir(static_dir):
            self.putChild(filen, nevow_File(os.path.join(static_dir, filen)))

        self.putChild("report_incident", IncidentReporter())

    # until we get rid of nevow.Page in favour of twisted.web.resource
    # we can't use getChild() -- but we CAN use childFactory or
    # override locatechild
    def childFactory(self, ctx, name):
        request = IRequest(ctx)
        return self.getChild(name, request)


    def getChild(self, path, request):
        if path == "helper_status":
            # the Helper isn't attached until after the Tub starts, so this child
            # needs to created on each request
            return status.HelperStatus(self.client.helper)
        if path == "storage":
            # Storage isn't initialized until after the web hierarchy is
            # constructed so this child needs to be created later than
            # `__init__`.
            try:
                storage_server = self.client.getServiceNamed("storage")
            except KeyError:
                storage_server = None
            return storage.StorageStatus(storage_server, self.client.nickname)


    # FIXME: This code is duplicated in root.py and introweb.py.
    def data_rendered_at(self, ctx, data):
        return render_time(time.time())

    def data_version(self, ctx, data):
        return get_package_versions_string()

    def data_import_path(self, ctx, data):
        return str(allmydata)

    def render_my_nodeid(self, ctx, data):
        tubid_s = "TubID: "+self.client.get_long_tubid()
        return T.td(title=tubid_s)[self.client.get_long_nodeid()]

    def data_my_nickname(self, ctx, data):
        return self.client.nickname


    def render_JSON(self, req):
        req.setHeader("content-type", "application/json; charset=utf-8")
        intro_summaries = [s.summary for s in self.client.introducer_connection_statuses()]
        sb = self.client.get_storage_broker()
        servers = self._describe_known_servers(sb)
        result = {
            "introducers": {
                "statuses": intro_summaries,
            },
            "servers": servers
        }
        return json.dumps(result, indent=1) + "\n"


    def _describe_known_servers(self, broker):
        return sorted(list(
            self._describe_server(server)
            for server
            in broker.get_known_servers()
        ))


    def _describe_server(self, server):
        status = server.get_connection_status()
        description = {
            u"nodeid": server.get_serverid(),
            u"connection_status": status.summary,
            u"available_space": server.get_available_space(),
            u"nickname": server.get_nickname(),
            u"version": None,
            u"last_received_data": status.last_received_time,
        }
        version = server.get_version()
        if version is not None:
            description[u"version"] = version["application-version"]

        return description


    def data_magic_folders(self, ctx, data):
        return self.client._magic_folders.keys()

    def render_magic_folder_row(self, ctx, data):
        magic_folder = self.client._magic_folders[data]
        (ok, messages) = magic_folder.get_public_status()
        ctx.fillSlots("magic_folder_name", data)
        if ok:
            ctx.fillSlots("magic_folder_status", "yes")
            ctx.fillSlots("magic_folder_status_alt", "working")
        else:
            ctx.fillSlots("magic_folder_status", "no")
            ctx.fillSlots("magic_folder_status_alt", "not working")

        status = T.ul(class_="magic-folder-status")
        for msg in messages:
            status[T.li[str(msg)]]
        return ctx.tag[status]

    def render_magic_folder(self, ctx, data):
        if not self.client._magic_folders:
            return T.p()
        return ctx.tag

    def render_services(self, ctx, data):
        ul = T.ul()
        try:
            ss = self.client.getServiceNamed("storage")
            stats = ss.get_stats()
            if stats["storage_server.accepting_immutable_shares"]:
                msg = "accepting new shares"
            else:
                msg = "not accepting new shares (read-only)"
            available = stats.get("storage_server.disk_avail")
            if available is not None:
                msg += ", %s available" % abbreviate_size(available)
            ul[T.li[T.a(href="storage")["Storage Server"], ": ", msg]]
        except KeyError:
            ul[T.li["Not running storage server"]]

        if self.client.helper:
            stats = self.client.helper.get_stats()
            active_uploads = stats["chk_upload_helper.active_uploads"]
            ul[T.li["Helper: %d active uploads" % (active_uploads,)]]
        else:
            ul[T.li["Not running helper"]]

        return ctx.tag[ul]

    def data_introducer_description(self, ctx, data):
        connected_count = self.data_connected_introducers( ctx, data )
        if connected_count == 0:
            return "No introducers connected"
        elif connected_count == 1:
            return "1 introducer connected"
        else:
            return "%s introducers connected" % (connected_count,)

    def data_total_introducers(self, ctx, data):
        return len(self.client.introducer_connection_statuses())

    def data_connected_introducers(self, ctx, data):
        return len([1 for cs in self.client.introducer_connection_statuses()
                    if cs.connected])

    def data_connected_to_at_least_one_introducer(self, ctx, data):
        if self.data_connected_introducers(ctx, data):
            return "yes"
        return "no"

    def data_connected_to_at_least_one_introducer_alt(self, ctx, data):
        return self._connectedalts[self.data_connected_to_at_least_one_introducer(ctx, data)]

    # In case we configure multiple introducers
    def data_introducers(self, ctx, data):
        return self.client.introducer_connection_statuses()

    def _render_connection_status(self, ctx, cs):
        connected = "yes" if cs.connected else "no"
        ctx.fillSlots("service_connection_status", connected)
        ctx.fillSlots("service_connection_status_alt",
                      self._connectedalts[connected])

        since = cs.last_connection_time
        ctx.fillSlots("service_connection_status_rel_time",
                      render_time_delta(since, self.now_fn())
                      if since is not None
                      else "N/A")
        ctx.fillSlots("service_connection_status_abs_time",
                      render_time_attr(since)
                      if since is not None
                      else "N/A")

        last_received_data_time = cs.last_received_time
        ctx.fillSlots("last_received_data_abs_time",
                      render_time_attr(last_received_data_time)
                      if last_received_data_time is not None
                      else "N/A")
        ctx.fillSlots("last_received_data_rel_time",
                      render_time_delta(last_received_data_time, self.now_fn())
                      if last_received_data_time is not None
                      else "N/A")

        others = cs.non_connected_statuses
        if cs.connected:
            ctx.fillSlots("summary", cs.summary)
            if others:
                details = "\n".join(["* %s: %s\n" % (which, others[which])
                                     for which in sorted(others)])
                ctx.fillSlots("details", "Other hints:\n" + details)
            else:
                ctx.fillSlots("details", "(no other hints)")
        else:
            details = T.ul()
            for which in sorted(others):
                details[T.li["%s: %s" % (which, others[which])]]
            ctx.fillSlots("summary", [cs.summary, details])
            ctx.fillSlots("details", "")

    def render_introducers_row(self, ctx, cs):
        self._render_connection_status(ctx, cs)
        return ctx.tag

    def data_helper_furl_prefix(self, ctx, data):
        try:
            uploader = self.client.getServiceNamed("uploader")
        except KeyError:
            return None
        furl, connected = uploader.get_helper_info()
        if not furl:
            return None
        # trim off the secret swissnum
        (prefix, _, swissnum) = furl.rpartition("/")
        return "%s/[censored]" % (prefix,)

    def data_helper_description(self, ctx, data):
        if self.data_connected_to_helper(ctx, data) == "no":
            return "Helper not connected"
        return "Helper"

    def data_connected_to_helper(self, ctx, data):
        try:
            uploader = self.client.getServiceNamed("uploader")
        except KeyError:
            return "no" # we don't even have an Uploader
        furl, connected = uploader.get_helper_info()

        if furl is None:
            return "not-configured"
        if connected:
            return "yes"
        return "no"

    def data_connected_to_helper_alt(self, ctx, data):
        return self._connectedalts[self.data_connected_to_helper(ctx, data)]

    def data_known_storage_servers(self, ctx, data):
        sb = self.client.get_storage_broker()
        return len(sb.get_all_serverids())

    def data_connected_storage_servers(self, ctx, data):
        sb = self.client.get_storage_broker()
        return len(sb.get_connected_servers())

    def data_services(self, ctx, data):
        sb = self.client.get_storage_broker()
        return sorted(sb.get_known_servers(), key=lambda s: s.get_serverid())

    def render_service_row(self, ctx, server):
        cs = server.get_connection_status()
        self._render_connection_status(ctx, cs)

        ctx.fillSlots("peerid", server.get_longname())
        ctx.fillSlots("nickname", server.get_nickname())

        announcement = server.get_announcement()
        version = announcement.get("my-version", "")
        available_space = server.get_available_space()
        if available_space is None:
            available_space = "N/A"
        else:
            available_space = abbreviate_size(available_space)
        ctx.fillSlots("version", version)
        ctx.fillSlots("available_space", available_space)

        return ctx.tag

    def render_download_form(self, ctx, data):
        # this is a form where users can download files by URI
        form = T.form(action="uri", method="get",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Download a file"],
            T.div["Tahoe-URI to download:"+SPACE,
                  T.input(type="text", name="uri")],
            T.div["Filename to download as:"+SPACE,
                  T.input(type="text", name="filename")],
            T.input(type="submit", value="Download!"),
            ]]
        return T.div[form]

    def render_view_form(self, ctx, data):
        # this is a form where users can download files by URI, or jump to a
        # named directory
        form = T.form(action="uri", method="get",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["View a file or directory"],
            "Tahoe-URI to view:"+SPACE,
            T.input(type="text", name="uri"), SPACE*2,
            T.input(type="submit", value="View!"),
            ]]
        return T.div[form]

    def render_upload_form(self, ctx, data):
        # This is a form where users can upload unlinked files.
        # Users can choose immutable, SDMF, or MDMF from a radio button.

        upload_chk  = T.input(type='radio', name='format',
                              value='chk', id='upload-chk',
                              checked='checked')
        upload_sdmf = T.input(type='radio', name='format',
                              value='sdmf', id='upload-sdmf')
        upload_mdmf = T.input(type='radio', name='format',
                              value='mdmf', id='upload-mdmf')

        form = T.form(action="uri", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Upload a file"],
            T.div["Choose a file:"+SPACE,
                  T.input(type="file", name="file", class_="freeform-input-file")],
            T.input(type="hidden", name="t", value="upload"),
            T.div[upload_chk,  T.label(for_="upload-chk") [" Immutable"],           SPACE,
                  upload_sdmf, T.label(for_="upload-sdmf")[" SDMF"],                SPACE,
                  upload_mdmf, T.label(for_="upload-mdmf")[" MDMF (experimental)"], SPACE*2,
                  T.input(type="submit", value="Upload!")],
            ]]
        return T.div[form]

    def render_mkdir_form(self, ctx, data):
        # This is a form where users can create new directories.
        # Users can choose SDMF or MDMF from a radio button.

        mkdir_sdmf = T.input(type='radio', name='format',
                             value='sdmf', id='mkdir-sdmf',
                             checked='checked')
        mkdir_mdmf = T.input(type='radio', name='format',
                             value='mdmf', id='mkdir-mdmf')

        form = T.form(action="uri", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.legend(class_="freeform-form-label")["Create a directory"],
            mkdir_sdmf, T.label(for_='mkdir-sdmf')[" SDMF"],                SPACE,
            mkdir_mdmf, T.label(for_='mkdir-mdmf')[" MDMF (experimental)"], SPACE*2,
            T.input(type="hidden", name="t", value="mkdir"),
            T.input(type="hidden", name="redirect_to_result", value="true"),
            T.input(type="submit", value="Create a directory"),
            ]]
        return T.div[form]

    def render_incident_button(self, ctx, data):
        # this button triggers a foolscap-logging "incident"
        form = T.form(action="report_incident", method="post",
                      enctype="multipart/form-data")[
            T.fieldset[
            T.input(type="hidden", name="t", value="report-incident"),
            "What went wrong?"+SPACE,
            T.input(type="text", name="details"), SPACE,
            T.input(type="submit", value=u"Save \u00BB"),
            ]]
        return T.div[form]
