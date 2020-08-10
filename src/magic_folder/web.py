import json

import attr

from nacl.encoding import (
    Base32Encoder,
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
from allmydata.util.hashutil import (
    timing_safe_compare,
)


def magic_folder_resource(magic_folder_state, get_auth_token, _v1_resource=None):
    """
    Create the root resource for the Magic Folder HTTP API.

    :param magic_folder_state: See ``magic_folder_web_service``.
    :param get_auth_token: See ``magic_folder_web_service``.

    :param IResource _v1_resource: A resource which will take the place of the
        usual bearer-token-authorized `/v1` resource.  This is intended to
        make testing easier.

    :return IResource: The resource that is the root of the HTTP API.
    """
    if _v1_resource is None:
        _v1_resource = APIv1(magic_folder_state)

    root = Resource()
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
class APIv1(Resource, object):
    """
    Implement the ``/v1`` HTTP API hierarchy.

    :ivar MagicFolderServiceState _magic_folder_state: The Magic Folder state
        to serve.
    """
    _magic_folder_state = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)
        self.putChild(b"magic-folder", MagicFolderAPIv1(self._magic_folder_state))


@attr.s
class MagicFolderAPIv1(Resource, object):
    """
    Implement the ``/v1/magic-folder`` HTTP API hierarchy.

    :ivar MagicFolderServiceState _magic_folder_state: The Magic Folder state
        to serve.
    """
    _magic_folder_state = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def render_GET(self, request):
        """
        Render a list of Magic Folders and some of their details, encoded as JSON.
        """
        request.responseHeaders.setRawHeaders(u"content-type", [u"application/json"])
        include_secret_information = int(request.args.get("include_secret_information", [0])[0])

        def get_folder_info(name, mf):
            info = {
                u"name": name,
                u"author": {
                    u"name": mf.author.name,
                    u"verify_key": mf.author.verify_key.encode(Base32Encoder),
                },
                u"stash_path": mf.stash_path.path,
                u"magic_path": mf.magic_path.path,
                u"poll_interval": mf.poll_interval,
                u"is_admin": mf.is_admin(),
            }
            if include_secret_information:
                info[u"author"][u"signing_key"] = mf.author.signing_key.encode(Base32Encoder)
                info[u"collective_dircap"] = mf.collective_dircap.encode("ascii")
                info[u"upload_dircap"] = mf.upload_dircap.encode("ascii")
            return info

        return json.dumps({
            u"folders": list(
                get_folder_info(name, config)
                for (name, config)
                in sorted(self._magic_folder_state.iter_magic_folder_configs())
            ),
        })


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


def magic_folder_web_service(web_endpoint, magic_folder_state, get_auth_token):
    """
    :param web_endpoint: a IStreamServerEndpoint where we should listen

    :param MagicFolderServiceState magic_folder_state: A reference to the
        shared magic folder state defining the service.

    :param get_auth_token: a callable that returns the current authentication token

    :returns: a StreamServerEndpointService instance
    """
    root = magic_folder_resource(magic_folder_state, get_auth_token)
    return StreamServerEndpointService(
        web_endpoint,
        Site(root),
    )


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
        return False
    auth_token = get_auth_token()
    expected = u"Bearer {}".format(auth_token).encode("ascii")
    return timing_safe_compare(
        authorization[0].encode("ascii"),
        expected,
    )
