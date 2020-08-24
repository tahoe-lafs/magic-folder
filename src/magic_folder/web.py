import json

import attr

from twisted.python.filepath import (
    FilePath,
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

from .magicpath import (
    magic2path,
)

def magic_folder_resource(get_auth_token, v1_resource):
    """
    Create the root resource for the Magic Folder HTTP API.

    :param get_auth_token: See ``magic_folder_web_service``.

    :param IResource v1_resource: A resource which will be protected by a
        bearer-token authorization scheme at the `v1` child of the resulting
        resource.

    :return IResource: The resource that is the root of the HTTP API.
    """
    root = Resource()
    root.putChild(
        b"v1",
        BearerTokenAuthorization(
            v1_resource,
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

    :ivar GlobalConfigDatabase _global_config: The global configuration for
        this Magic Folder service.
    """
    _global_config = attr.ib()
    _global_service = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)
        self.putChild(b"magic-folder", MagicFolderAPIv1(self._global_config))
        self.putChild(b"snapshot", SnapshotAPIv1(self._global_config, self._global_service))


@attr.s
class SnapshotAPIv1(Resource, object):
    """
    ``SnapshotAPIv1`` implements the ``/v1/snapshot`` portion of the HTTP API
    resource hierarchy.

    :ivar GlobalConfigDatabase _global_config: The global configuration for
        this Magic Folder service.
    """
    _global_config = attr.ib()
    _global_service = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def render_GET(self, request):
        """
        Respond with all of the snapshots for all of the files in all of the
        folders.
        """
        application_json(request)
        return json.dumps(dict(list_all_snapshots(self._global_config)))

    def getChild(self, name, request):
        name_u = name.decode("utf-8")
        folder_config = self._global_config.get_magic_folder(name_u)
        folder_service = self._global_service.get_folder_service(name_u)
        return MagicFolderSnapshotAPIv1(folder_config, folder_service)


@attr.s
class MagicFolderSnapshotAPIv1(Resource, object):
    """
    """
    _folder_config = attr.ib()
    _folder_service = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def render_POST(self, request):
        path_u = request.args[b"path"][0].decode("utf-8")

        # preauthChild on user input bypasses the primary safety feature of
        # FilePath so it's a bad idea.  However, add_file is going to apply an
        # equivalent safety check to the one we're bypassing.  So ... it's
        # okay.
        #
        # Maybe we should have a "relative path" type that has safety features
        # in it and then any safety checks can be performed early, near the
        # code accepting user input, instead of deeper in the model.
        path = self._folder_config.magic_path.preauthChild(path_u)

        # TODO error handling?
        adding = self._folder_service.local_snapshot_service.add_file(path)

        request.setResponseCode(http.CREATED)
        application_json(request)
        return b"{}"


def application_json(request):
    request.responseHeaders.setRawHeaders(u"content-type", [u"application/json"])


def list_all_snapshots(global_config):
    for name in global_config.list_magic_folders():
        folder_config = global_config.get_magic_folder(name)
        yield name, dict(list_all_folder_snapshots(folder_config))


def list_all_folder_snapshots(folder_config):
    for snapshot_path in folder_config.get_all_localsnapshot_paths():
        absolute_path = magic2path(snapshot_path)
        if not absolute_path.startswith(folder_config.magic_path.path):
            raise ValueError(
                "Found path {!r} in local snapshot database for magic-folder {!r} "
                "that is outside of local magic folder directory {!r}.".format(
                    absolute_path,
                    folder_config.name,
                    folder_config.magic_path.path,
                ),
            )
        relative_segments = FilePath(absolute_path).segmentsFrom(folder_config.magic_path)
        relative_path = u"/".join(relative_segments)
        yield relative_path, list_all_path_snapshots(folder_config, snapshot_path)


def snapshot_to_json(snapshot):
    result = {
        u"type": u"local",
        # XXX Probably want to populate parents with something ...
        u"parents": [],
        u"content-path": snapshot.content_path.path,
        u"identifier": unicode(snapshot.identifier),
        u"author": snapshot.author.to_remote_author().to_json(),
    }
    return result


def list_all_path_snapshots(folder_config, snapshot_path):
    top_snapshot = folder_config.get_local_snapshot(snapshot_path)
    snapshots = list(
        snapshot_to_json(snapshot)
        for snapshot
        in unwind_snapshots(top_snapshot)
    )
    return snapshots


def unwind_snapshots(snapshot):
    yield snapshot
    for p in snapshot.parents_local:
        for parent_snapshot in unwind_snapshots(p):
            yield parent_snapshot


@attr.s
class MagicFolderAPIv1(Resource, object):
    """
    Implement the ``/v1/magic-folder`` HTTP API hierarchy.

    :ivar GlobalConfigDatabase _global_config: The global configuration for
        this Magic Folder service.
    """
    _global_config = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def render_GET(self, request):
        """
        Render a list of Magic Folders and some of their details, encoded as JSON.
        """
        application_json(request)
        def magic_folder_details():
            for name in sorted(self._global_config.list_magic_folders()):
                config = self._global_config.get_magic_folder(name)
                yield {
                    u"name": name,
                    u"local-path": config.magic_path.path,
                }

        return json.dumps({
            u"folders": list(magic_folder_details()),
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


def magic_folder_web_service(web_endpoint, global_config, global_service, get_auth_token):
    """
    :param web_endpoint: a IStreamServerEndpoint where we should listen

    :param GlobalConfigDatabase global_config: A reference to the shared magic
        folder state defining the service.

    :param get_auth_token: a callable that returns the current authentication token

    :returns: a StreamServerEndpointService instance
    """
    v1_resource = APIv1(global_config, global_service)
    root = magic_folder_resource(get_auth_token, v1_resource)
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
