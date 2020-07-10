import json
import cgi
from json import (
    dumps,
)
import attr

from twisted.application.internet import (
    StreamServerEndpointService,
)
from twisted.web.server import (
    Site,
    NOT_DONE_YET,
)
from twisted.web import (
    http,
)
from twisted.web.resource import (
    Resource,
    NoResource,
)
from allmydata.util.hashutil import (
    timing_safe_compare,
)


def magic_folder_resource(get_magic_folder, get_auth_token):
    """
    Create the root resource for the Magic Folder HTTP API.

    :param get_magic_folder: See ``magic_folder_web_service``.
    :param get_auth_token: See ``magic_folder_web_service``.

    :return IResource: The resource that is the root of the HTTP API.
    """
    root = Resource()
    root.putChild(b"api", MagicFolderWebApi(get_magic_folder, get_auth_token))
    root.putChild(b"v1", V1MagicFolderAPI(get_magic_folder, get_auth_token))
    return root


@attr.s
class V1MagicFolderAPI(Resource, object):
    """
    The root of the ``/v1`` HTTP API hierarchy.

    :ivar (unicode -> MagicFolder) _get_magic_folder: A function that looks up
        a magic folder by its nickname.

    :ivar (IO bytes) _get_auth_token: A function that returns the correct
        authentication token.
    """
    _get_magic_folder = attr.ib()
    _get_auth_token = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def getChild(self, name, request):
        """
        The direct children of ``V1MagicFolderAPI`` are resources that correspond
        to individual Magic Folders.
        """
        try:
            magic_folder = self._get_magic_folder(name.decode("utf-8"))
        except KeyError:
            return NoResource()
        return V1MagicFolder(magic_folder, self._get_auth_token)


@attr.s
class V1MagicFolder(Resource, object):
    _magic_folder = attr.ib()
    _get_auth_token = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)
        self.putChild(b"snapshots", V1Snapshots(self._magic_folder, self._get_auth_token))


def _snapshot_json(snapshot):
    snapshot_json = {
        u"name": snapshot.name,
        u"author": snapshot.author.name,
        u"content-path": snapshot.content_path,
        u"parents": list(parent.id() for parent in snapshot.parents_local),
    }
    snapshot_id = _snapshot_id(snapshot_json)
    snapshot_json[u"id"] = snapshot_id
    return snapshot_json


@attr.s
class V1Snapshots(Resource, object):
    _magic_folder = attr.ib()
    _get_auth_token = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def render_GET(self, request):
        request.responseHeaders.setRawHeaders(u"content-type", [u"application/json"])
        return dumps({
            u"snapshots": list(
                _snapshot_json(snapshot)
                for snapshot
                in self._magic_folder.model.query_snapshots()
            ),
        })


def magic_folder_web_service(web_endpoint, get_magic_folder, get_auth_token):
    """
    :param web_endpoint: a IStreamServerEndpoint where we should listen

    :param get_magic_folder: a callable that returns a MagicFolder given a name

    :param get_auth_token: a callable that returns the current authentication token

    :returns: a StreamServerEndpointService instance
    """
    root = magic_folder_resource(get_magic_folder, get_auth_token)
    return StreamServerEndpointService(
        web_endpoint,
        Site(root),
    )


def error(request, code, message):
    request.setResponseCode(code, message)
    request.finish()

def authorize(request, get_auth_token):
    if "token" in request.args:
        error(
            request,
            http.BAD_REQUEST,
            "Do not pass 'token' as URL argument",
        )
        return False

    t = request.content.tell()
    request.content.seek(0)
    fields = cgi.FieldStorage(
        request.content,
        {k: vs[0]
         for (k, vs)
         in request.requestHeaders.getAllRawHeaders()
        },
        environ={'REQUEST_METHOD': 'POST'},
    )
    request.content.seek(t)

    # not using get_arg() here because we *don't* want the token
    # argument to work if you passed it as a GET-style argument
    token = None
    if fields and 'token' in fields:
        token = fields['token'].value.strip()
    if not token:
        error(request, http.UNAUTHORIZED, "Missing token")
        return False
    if not timing_safe_compare(token, get_auth_token()):
        error(request, http.UNAUTHORIZED, "Invalid token")
        return False

    return True


class MagicFolderWebApi(Resource):
    """
    I provide the web-based API for Magic Folder status etc.
    """

    def __init__(self, get_magic_folder, get_auth_token):
        Resource.__init__(self)
        self.get_magic_folder = get_magic_folder
        self.get_auth_token = get_auth_token

    def render_POST(self, request):
        if not authorize(request, self.get_auth_token):
            return NOT_DONE_YET

        request.setHeader("content-type", "application/json")
        nick = request.args.get("name", ["default"])[0]

        try:
            magic_folder = self.get_magic_folder(nick)
        except KeyError:
            request.setResponseCode(http.NOT_FOUND)
            return json.dumps({
                u"error":
                u"No such magic-folder: {}".format(
                    nick.decode("utf-8"),
                ),
            })

        data = []
        for item in magic_folder.uploader.get_status():
            data.append(status_for_item("upload", item))

        for item in magic_folder.downloader.get_status():
            data.append(status_for_item("download", item))

        return json.dumps(data)


def status_for_item(kind, item):
    d = dict(
        path=item.relpath_u,
        status=item.status_history()[-1][0],
        kind=kind,
    )
    for (status, ts) in item.status_history():
        d[status + '_at'] = ts
    d['percent_done'] = item.progress.progress
    d['size'] = item.size
    return d
