import json
import cgi

from twisted.internet.endpoints import (
    serverFromString,
)
from twisted.application.internet import (
    StreamServerEndpointService,
)
from twisted.web.server import (
    Site,
)
from twisted.web import (
    http,
)
from twisted.web.resource import (
    Resource,
)
from allmydata.web.common import (
    WebError,
)
from allmydata.util.hashutil import (
    timing_safe_compare,
)

def magic_folder_web_service(reactor, webport, get_magic_folder, get_auth_token):
    root = Resource()
    root.putChild(b"api", MagicFolderWebApi(get_magic_folder, get_auth_token))
    return StreamServerEndpointService(
        serverFromString(reactor, webport),
        Site(root),
    )

def authorize(request, get_auth_token):
    if "token" in request.args:
        raise WebError(
            "Do not pass 'token' as URL argument",
            http.BAD_REQUEST,
        )

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
        raise WebError("Missing token", http.UNAUTHORIZED)
    if not timing_safe_compare(token, get_auth_token()):
        raise WebError("Invalid token", http.UNAUTHORIZED)


class MagicFolderWebApi(Resource):
    """
    I provide the web-based API for Magic Folder status etc.
    """

    def __init__(self, get_magic_folder, get_auth_token):
        Resource.__init__(self)
        self.get_magic_folder = get_magic_folder
        self.get_auth_token = get_auth_token

    def render_POST(self, request):
        authorize(request, self.get_auth_token)

        request.setHeader("content-type", "application/json")
        nick = request.args.get("name", ["default"])[0]

        try:
            magic_folder = self.get_magic_folder(nick)
        except KeyError:
            raise WebError(
                "No such magic-folder '{}'".format(nick),
                404,
            )

        data = []
        for item in magic_folder.uploader.get_status():
            d = dict(
                path=item.relpath_u,
                status=item.status_history()[-1][0],
                kind='upload',
            )
            for (status, ts) in item.status_history():
                d[status + '_at'] = ts
            d['percent_done'] = item.progress.progress
            d['size'] = item.size
            data.append(d)

        for item in magic_folder.downloader.get_status():
            d = dict(
                path=item.relpath_u,
                status=item.status_history()[-1][0],
                kind='download',
            )
            for (status, ts) in item.status_history():
                d[status + '_at'] = ts
            d['percent_done'] = item.progress.progress
            d['size'] = item.size
            data.append(d)

        return json.dumps(data)
