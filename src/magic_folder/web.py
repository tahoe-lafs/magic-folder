from __future__ import unicode_literals

import json

import attr

from nacl.encoding import (
    Base32Encoder,
)

from twisted.python.filepath import (
    InsecurePath,
)
from twisted.internet.defer import (
    maybeDeferred,
)
from twisted.application.internet import (
    StreamServerEndpointService,
)
from twisted.web.server import (
    NOT_DONE_YET,
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
    unmangle_path_segments,
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
        _application_json(request)
        return json.dumps(dict(_list_all_snapshots(self._global_config)))

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

        # preauthChild allows path-separators in the "path" (i.e. not
        # just a single path-segment). That is precisely what we want
        # here, though. It sill does not allow the path to "jump out"
        # of the base magic_path -- that is, an InsecurePath error
        # will result if you pass an absolute path outside the folder
        # or a relative path that reaches up too far.

        try:
            path = self._folder_config.magic_path.preauthChild(path_u)
        except InsecurePath as e:
            request.setResponseCode(http.NOT_ACCEPTABLE)
            _application_json(request)
            return json.dumps({u"reason": str(e)})

        adding = maybeDeferred(
            self._folder_service.local_snapshot_service.add_file,
            path,
        )
        def added(ignored):
            request.setResponseCode(http.CREATED)
            _application_json(request)
            request.write(b"{}")

        def failed(reason):
            request.setResponseCode(http.INTERNAL_SERVER_ERROR)
            _application_json(request)
            request.write(json.dumps({u"reason": reason.getErrorMessage()}))

        adding.addCallbacks(
            added,
            failed,
        ).addCallback(
            lambda ignored: request.finish(),
        )
        return NOT_DONE_YET


def _application_json(request):
    request.responseHeaders.setRawHeaders(u"content-type", [u"application/json"])


def _list_all_snapshots(global_config):
    """
    Get all snapshots for all files in all magic folders known to the given
    configuration.

    :param GlobalConfigDatabase global_config: The Magic Folder daemon
        configuration to inspect.

    :return: A generator of two-tuples.  The first element of each tuple is a
        unicode string giving the name of a magic-folder.  The second element
        is a dictionary mapping relative paths to snapshot lists.
    """
    for name in global_config.list_magic_folders():
        folder_config = global_config.get_magic_folder(name)
        yield name, dict(_list_all_folder_snapshots(folder_config))


def _list_all_folder_snapshots(folder_config):
    """
    Get all snapshots for all files contained by the given folder.

    XXX This only returns local snapshots.

    :param MagicFolderConfig folder_config: The magic-folder for which to look
        up snapshots.

    :return: A generator of two-tuples.  The first element of each tuple is a
        unicode string giving a path relative to the local filesystem
        container for ``folder_config``.  The second element is a list
        representing all snapshots for that file.
    """
    for snapshot_path in folder_config.get_all_localsnapshot_paths():
        relative_segments = unmangle_path_segments(snapshot_path)
        relative_path = u"/".join(relative_segments)
        yield relative_path, _list_all_path_snapshots(folder_config, snapshot_path)


def _list_all_path_snapshots(folder_config, snapshot_path):
    """
    Get all snapshots for the given path in the given folder.

    :param MagicFolderConfig folder_config: The magic-folder to consider.

    :param FilePath snapshot_path: The path of a file within the magic-folder.

    :return list: A JSON-compatible representation of all discovered
        snapshots.
    """
    top_snapshot = folder_config.get_local_snapshot(snapshot_path)
    snapshots = list(
        _snapshot_to_json(snapshot)
        for snapshot
        in _flatten_snapshots(top_snapshot)
    )
    return snapshots


def _flatten_snapshots(snapshot):
    """
    Yield ``snapshot`` and all of its ancestors.

    :param LocalSnapshot snapshot: The starting snapshot.

    :return: A generator that starts with ``snapshot`` and then proceeds to
        its parents, and the parents of those snapshots, and so on.
    """
    yield snapshot
    for p in snapshot.parents_local:
        for parent_snapshot in _flatten_snapshots(p):
            yield parent_snapshot


def _snapshot_to_json(snapshot):
    """
    Create a JSON-compatible representation of a single snapshot.

    :param LocalSnapshot snapshot: The snapshot to represent.

    :return dict: A dictionary which can be mapped to JSON which completely
        represents the given snapshot.
    """
    result = {
        u"type": u"local",
        # XXX Probably want to populate parents with something ...
        u"parents": [],
        u"content-path": snapshot.content_path.path,
        u"identifier": unicode(snapshot.identifier),
        u"author": snapshot.author.to_remote_author().to_json(),
    }
    return result


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
        include_secret_information = int(request.args.get("include_secret_information", [0])[0])
        _application_json(request)  # set reply headers

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
                info[u"collective_dircap"] = mf.collective_dircap
                info[u"upload_dircap"] = mf.upload_dircap
            return info

        def all_folder_configs():
            for name in sorted(self._global_config.list_magic_folders()):
                yield (name, self._global_config.get_magic_folder(name))

        return json.dumps({
            name: get_folder_info(name, config)
            for name, config
            in all_folder_configs()
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
