import json
import cgi

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
)
from allmydata.util.hashutil import (
    timing_safe_compare,
)


def magic_folder_resource(get_magic_folder, get_auth_token, _v1_resource=None):
    """
    Create the root resource for the Magic Folder HTTP API.

    :param get_magic_folder: See ``magic_folder_web_service``.
    :param get_auth_token: See ``magic_folder_web_service``.

    :param IResource _v1_resource: A resource which will take the place of the
        usual bearer-token-authorized `/v1` resource.  This is intended to
        make testing easier.

    :return IResource: The resource that is the root of the HTTP API.
    """
    if _v1_resource is None:
        _v1_resource = V1MagicFolderAPI(get_magic_folder)

    root = Resource()
    root.putChild(
        b"api",
        MagicFolderWebApi(get_magic_folder, get_auth_token),
    )
    root.putChild(
        b"v1",
        BearerTokenAuthorization(
            _v1_resource,
            get_auth_token,
        ),
    )
    return root


@attr.s
class BearerTokenAuthorization(Resource, object):
    """
    Protect a resource hierarchy with bearer-token based authorization.

    :ivar IResource _resource: The root of a resource hierarchy to which to
        delegate actual rendering.

    :ivar (IO bytes) _get_auth_token: A function that returns the correct
        authentication token.
    """
    _resource = attr.ib()
    _get_auth_token = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def render(self, request):
        """
        Render the wrapped resource if the request carries correct authorization.

        If it does not, render an UNAUTHORIZED response.
        """
        if _is_authorized(request, self._get_auth_token):
            # Authorization checks out, let the protected resource do what it
            # will.
            return self._resource.render(request)
        # Don't let anything through that isn't authorized.
        return unauthorized(request)

    def getChildWithDefault(self, path, request):
        """
        Get the request child from the wrapped resource if the request carries
        correct authorization.

        If it does not, return an ``Unauthorized`` resource.
        """
        if _is_authorized(request, self._get_auth_token):
            # Authorization checks out, let the protected resource do what it
            # will.
            return self._resource.getChildWithDefault(path, request)
        # Don't let anything through that isn't authorized.
        return Unauthorized()


@attr.s
class V1MagicFolderAPI(Resource, object):
    """
    The root of the ``/v1`` HTTP API hierarchy.

    :ivar (unicode -> MagicFolder) _get_magic_folder: A function that looks up
        a magic folder by its nickname.
    """
    _get_magic_folder = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)


class Unauthorized(Resource):
    """
    An ``Unauthorized`` resource renders an HTTP *UNAUTHORIZED* response for
    all requests it handles (including for child resources of itself).
    """
    isLeaf = True

    def render(self, request):
        return unauthorized(request)


def unauthorized(request):
    """
    Render an HTTP *UNAUTHORIZED* response to the given request.
    """
    request.setResponseCode(http.UNAUTHORIZED)
    return b""


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


def _is_authorized(request, get_auth_token):
    """
    Check the authorization carried by the given request.

    :param IRequest request: The request object being considered.  If it has
        an *Authorization* header carrying a *Bearer token* matching the token
        returned by ``get_auth_token`` the request is considered authorized,
        otherwise it is not.

    :param (IO bytes) get_auth_token: A callable which returns the
        authorization token value which is required for requests to be
        authorized.

    :return bool: True if and only if the given request contains the required
        authorization materials.
    """
    authorization = request.requestHeaders.getRawHeaders(u"authorization")
    if authorization is None or len(authorization) == 0:
        return False
    if len(authorization) > 1:
        # XXX Untested
        raise ValueError("Scammy")
    auth_token = get_auth_token()
    if not auth_token:
        # XXX Untested
        return False
    expected = u"Bearer {}".format(auth_token).encode("ascii")
    return timing_safe_compare(
        authorization[0].encode("ascii"),
        expected,
    )


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
