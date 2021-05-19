from __future__ import unicode_literals

import os
import json

import attr

from nacl.signing import (
    VerifyKey,
)
from nacl.encoding import (
    Base32Encoder,
)

from twisted.python.filepath import (
    FilePath,
    InsecurePath,
)
from twisted.internet.defer import (
    maybeDeferred,
)
from twisted.application.internet import (
    StreamServerEndpointService,
)
from twisted.web.resource import (
    NoResource,
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
from allmydata.uri import (
    from_string as tahoe_uri_from_string,
)
from allmydata.interfaces import (
    IDirnodeURI,
)
from allmydata.util.hashutil import (
    timing_safe_compare,
)

from .magicpath import (
    magic2path,
)
from .snapshot import (
    create_author,
)
from .participants import (
    participants_from_collective,
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
    _tahoe_client = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)
        self.putChild(b"magic-folder", MagicFolderAPIv1(self._global_config))
        self.putChild(b"snapshot", SnapshotAPIv1(self._global_config, self._global_service))
        self.putChild(b"participants", ParticipantsAPIv1(self._global_config, self._tahoe_client))


@attr.s
class ParticipantsAPIv1(Resource, object):
    """
    Implements the ``/v1/participants`` portion of the HTTP API
    resource hierarchy.

    :ivar GlobalConfigDatabase _global_config: The global configuration for
        this Magic Folder service.
    """
    _global_config = attr.ib()
    _tahoe_client = attr.ib()

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def getChild(self, name, request):
        name_u = name.decode("utf-8")
        try:
            folder_config = self._global_config.get_magic_folder(name_u)
        except ValueError:
            return NoResource(b"{}")
        return MagicFolderParticipantAPIv1(folder_config, self._tahoe_client)


@attr.s
class MagicFolderParticipantAPIv1(Resource, object):
    """
    Implements the v1 API for the ``/participants/<magic-folder-name>``
    part of the API hierarchy.
    """
    _folder_config = attr.ib()
    _tahoe_client = attr.ib()

    # XXX maybe we could/should pass around a TahoeClient with the
    # global-config? It could maybe simplify stuff, *so long as* we
    # can still override a "testing" client into e.g. a "testing"
    # config...

    def __attrs_post_init__(self):
        Resource.__init__(self)

    def render_GET(self, request):
        """
        List all participants of this folder
        """
        collective = participants_from_collective(
            self._folder_config.collective_dircap,
            self._folder_config.upload_dircap,
            self._tahoe_client,
        )
        d = collective.list()

        def listed(participants):
            reply = {
                part.name: {
                    "personal_dmd": part.dircap.decode("ascii"),
                    # not tracked properly yet
                    # "public_key": part.verify_key.encode(Base32Encoder),
                }
                for part in participants
            }
            request.setResponseCode(http.OK)
            _application_json(request)
            request.write(json.dumps(reply).encode("utf8"))
        d.addCallback(listed)

        def failed(reason):
            # probably should log this failure
            request.setResponseCode(http.INTERNAL_SERVER_ERROR)
            _application_json(request)
            request.write(json.dumps({"reason": "unexpected error processing request"}))
            return None
        d.addErrback(failed)
        d.addBoth(lambda ignored: request.finish())
        return NOT_DONE_YET

    def render_POST(self, request):
        """
        Add a new participant to this folder with details from the JSON-encoded body.
        """
        body = request.content.read()
        try:
            participant = json.loads(body)
            required_keys = {
                "author",
                "personal_dmd",
            }
            required_author_keys = {
                "name",
                # not yet
                # "public_key_base32",
            }
            if set(participant.keys()) != required_keys:
                raise ValueError("Require input: {}".format(", ".join(sorted(required_keys))))
            if set(participant["author"].keys()) != required_author_keys:
                raise ValueError("'author' requires: {}".format(", ".join(sorted(required_author_keys))))

            author = create_author(
                participant["author"]["name"],
                # we don't yet properly track keys but need one
                # here .. this won't be correct, but we won't use
                # it .. following code still only looks at the
                # .name attribute
                VerifyKey(os.urandom(32)),
            )

            dmd = tahoe_uri_from_string(participant["personal_dmd"])
            if not IDirnodeURI.providedBy(dmd):
                raise ValueError("personal_dmd must be a directory-capability")
            if not dmd.is_readonly():
                raise ValueError("personal_dmd must be read-only")
            personal_dmd_cap = participant["personal_dmd"]
        except ValueError as e:
            request.setResponseCode(http.BAD_REQUEST)
            return json.dumps({"reason": str(e)})

        collective = participants_from_collective(
            self._folder_config.collective_dircap,
            self._folder_config.upload_dircap,
            self._tahoe_client,
        )
        d = collective.add(author, personal_dmd_cap)

        def added(ignored):
            request.setResponseCode(http.CREATED)
            _application_json(request)
            request.write(b"{}")
            return None
        d.addCallback(added)

        def failed(reason):
            request.setResponseCode(http.INTERNAL_SERVER_ERROR)
            _application_json(request)
            if isinstance(reason.value, ValueError):
                request.write(json.dumps({"reason": str(reason.value)}))
            else:
                # probably should log this error
                request.write(json.dumps({"reason": "unexpected error processing request"}))
            return None
        d.addErrback(failed)
        d.addBoth(lambda ignored: request.finish())

        return NOT_DONE_YET


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
        """
        Create a new Snapshot
        """
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
            # XXX log this?
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


def magic_folder_web_service(web_endpoint, global_config, global_service, get_auth_token, tahoe_client):
    """
    :param web_endpoint: a IStreamServerEndpoint where we should listen

    :param GlobalConfigDatabase global_config: A reference to the shared magic
        folder state defining the service.

    :param get_auth_token: a callable that returns the current authentication token

    :param TahoeClient tahoe_client: a way to access Tahoe-LAFS

    :returns: a StreamServerEndpointService instance
    """
    v1_resource = APIv1(global_config, global_service, tahoe_client)
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
