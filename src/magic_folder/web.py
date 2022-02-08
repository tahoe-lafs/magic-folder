
import os
import json

import attr

from nacl.signing import (
    VerifyKey,
)
from nacl.encoding import (
    Base32Encoder,
)

from autobahn.twisted.resource import (
    WebSocketResource,
)

from eliot import (
    write_failure,
)
from eliot.twisted import inline_callbacks

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    returnValue,
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

from werkzeug.exceptions import HTTPException

from klein import Klein

from cryptography.hazmat.primitives.constant_time import bytes_eq as timing_safe_compare

from .common import APIError
from .status import (
    StatusFactory,
    IStatus,
)
from .tahoe_client import (
    InsufficientStorageServers,
)
from .snapshot import (
    create_author,
)
from .util.capabilities import (
    Capability,
)
from .util.file import (
    ns_to_seconds,
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
            # This resource should be transparent, so put the
            # segement this resource is was expected to consume.
            # See twisted.web.resource.getChildForRequest.
            request.postpath.insert(0, request.prepath.pop())
            # Authorization checks out, let the protected resource do what it
            # will.
            return self._resource
        # Don't let anything through that isn't authorized.
        return Unauthorized()

def _load_json(body):
    """
    Load json from body, raising :py:`APIError` on failure.
    """
    try:
        return json.loads(body)
    except ValueError as e:
        raise APIError.from_exception(http.BAD_REQUEST, e, prefix="Could not load JSON")

@attr.s
class APIv1(object):
    """
    Implement the ``/v1`` HTTP API hierarchy.

    :ivar GlobalConfigDatabase _global_config: The global configuration for
        this Magic Folder service.
    """
    _global_config = attr.ib()
    _global_service = attr.ib()  # MagicFolderService instance
    _status_service = attr.ib(validator=attr.validators.provides(IStatus))

    app = Klein()

    @app.handle_errors(APIError)
    def handle_api_error(self, request, failure):
        exc = failure.value
        request.setResponseCode(exc.code or http.INTERNAL_SERVER_ERROR)
        _application_json(request)
        return json.dumps(exc.to_json()).encode("utf8")

    @app.handle_errors(HTTPException)
    def handle_http_error(self, request, failure):
        """
        Convert a werkzeug py:`HTTPException` to a json response.

        This mirrors the default code in klein for handling these
        exceptions, but generates a json body.
        """
        exc = failure.value
        request.setResponseCode(exc.code, exc.name.encode("utf8"))
        resp = exc.get_response({})
        for header, value in resp.headers.items(lower=True):
            # We skip these, since we have our own content.
            if header in (b"content-length", b"content-type"):
                continue
            request.setHeader(header, value)
        _application_json(request)
        return json.dumps({"reason": exc.description}).encode("utf8")

    @app.handle_errors(InsufficientStorageServers)
    def no_servers(self, request, failure):
        write_failure(failure)
        request.setResponseCode(http.INTERNAL_SERVER_ERROR)
        _application_json(request)
        return json.dumps({"reason": str(failure.value)}).encode("utf8")

    @app.handle_errors(Exception)
    def fallback_error(self, request, failure):
        """
        Turn unknown exceptions into 500 errors, and log the failure.
        """
        write_failure(failure)
        request.setResponseCode(http.INTERNAL_SERVER_ERROR)
        _application_json(request)
        return json.dumps({"reason": "unexpected error processing request"}).encode("utf8")

    @app.route("/status")
    def status(self, request):
        return WebSocketResource(StatusFactory(self._status_service))

    @app.route("/magic-folder", methods=["POST"])
    @inline_callbacks
    def add_magic_folder(self, request):
        """
        Add a new magic folder.
        """
        body = request.content.read()
        data = _load_json(body)

        yield self._global_service.create_folder(
            data['name'],
            data['author_name'],
            FilePath(data['local_path']),
            data['poll_interval'],
            data['scan_interval'],
        )

        _application_json(request)
        returnValue(b"{}")

    @app.route("/magic-folder/<string:folder_name>", methods=["DELETE"])
    @inline_callbacks
    def leave_magic_folder(self, request, folder_name):
        """
        Leave a new magic folder.
        """
        body = request.content.read()
        data = _load_json(body)

        yield self._global_service.leave_folder(
            folder_name,
            really_delete_write_capability=data.get(
                "really-delete-write-capability", False
            ),
        )

        _application_json(request)
        returnValue(b"{}")

    @app.route("/magic-folder/<string:folder_name>/participants", methods=['GET'])
    @inline_callbacks
    def list_participants(self, request, folder_name):
        """
        List all participants of this folder
        """
        folder_service = self._global_service.get_folder_service(folder_name)

        participants = yield folder_service.participants()

        reply = {
            part.name: {
                "personal_dmd": part.dircap.danger_real_capability_string(),
                # not tracked properly yet
                # "public_key": part.verify_key.encode(Base32Encoder),
            }
            for part in participants
        }
        request.setResponseCode(http.OK)
        _application_json(request)
        returnValue(json.dumps(reply).encode("utf8"))

    @app.route("/magic-folder/<string:folder_name>/participants", methods=['POST'])
    @inline_callbacks
    def add_participant(self, request, folder_name):
        """
        Add a new participant to this folder with details from the JSON-encoded body.
        """
        folder_service = self._global_service.get_folder_service(folder_name)

        body = request.content.read()
        participant = _load_json(body)

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
            raise _InputError("Require input: {}".format(", ".join(sorted(required_keys))))
        if set(participant["author"].keys()) != required_author_keys:
            raise _InputError("'author' requires: {}".format(", ".join(sorted(required_author_keys))))

        author = create_author(
            participant["author"]["name"],
            # we don't yet properly track keys but need one
            # here .. this won't be correct, but we won't use
            # it .. following code still only looks at the
            # .name attribute
            # see https://github.com/LeastAuthority/magic-folder/issues/331
            VerifyKey(os.urandom(32)),
        )

        personal_dmd_cap = Capability.from_string(participant["personal_dmd"])

        if not personal_dmd_cap.is_readonly_directory():
            raise _InputError("personal_dmd must be a read-only directory capability.")

        yield folder_service.add_participant(author, personal_dmd_cap)

        request.setResponseCode(http.CREATED)
        _application_json(request)
        returnValue(b"{}")

    @app.route("/snapshot", methods=['GET'])
    def list_all_sanpshots(self, request):
        """
        Respond with all of the snapshots for all of the files in all of the
        folders.
        """
        _application_json(request)
        return json.dumps(dict(_list_all_snapshots(self._global_config))).encode("utf8")

    @app.route("/magic-folder/<string:folder_name>/scan-local", methods=['PUT'])
    @inline_callbacks
    def scan_folder_local(self, request, folder_name):
        """
        Request an immediate local scan on a particular folder
        """
        folder_service = self._global_service.get_folder_service(folder_name)

        yield folder_service.scan_local()

        _application_json(request)
        returnValue(b"{}")

    @app.route("/magic-folder/<string:folder_name>/poll-remote", methods=['PUT'])
    @inline_callbacks
    def poll_folder_remote(self, request, folder_name):
        """
        Request an immediate remote poll on a particular folder
        """
        folder_service = self._global_service.get_folder_service(folder_name)

        yield folder_service.poll_remote()

        _application_json(request)
        returnValue(b"{}")

    @app.route("/magic-folder/<string:folder_name>/snapshot", methods=['POST'])
    @inline_callbacks
    def add_snapshot(self, request, folder_name):
        """
        Create a new Snapshot
        """
        folder_service = self._global_service.get_folder_service(folder_name)

        path = request.args[b"path"][0].decode("utf-8")

        yield folder_service.add_snapshot(path)

        request.setResponseCode(http.CREATED)
        _application_json(request)
        returnValue(b"{}")

    @app.route("/magic-folder", methods=["GET"])
    def list_folders(self, request):
        """
        Render a list of Magic Folders and some of their details, encoded as JSON.
        """
        include_secret_information = int(request.args.get(b"include_secret_information", [0])[0])
        _application_json(request)  # set reply headers

        def get_folder_info(name, mf):
            info = {
                u"name": name,
                u"author": {
                    u"name": mf.author.name,
                    u"verify_key": mf.author.verify_key.encode(Base32Encoder).decode("ascii"),
                },
                u"stash_path": mf.stash_path.path,
                u"magic_path": mf.magic_path.path,
                u"poll_interval": mf.poll_interval,
                u"scan_interval": mf.scan_interval,
                u"is_admin": mf.is_admin(),
            }
            if include_secret_information:
                info[u"author"][u"signing_key"] = mf.author.signing_key.encode(Base32Encoder).decode("ascii")
                info[u"collective_dircap"] = mf.collective_dircap.danger_real_capability_string()
                info[u"upload_dircap"] = mf.upload_dircap.danger_real_capability_string()
            return info

        def all_folder_configs():
            for name in sorted(self._global_config.list_magic_folders()):
                yield (name, self._global_config.get_magic_folder(name))

        return json.dumps({
            name: get_folder_info(name, config)
            for name, config
            in all_folder_configs()
        }).encode("utf8")

    @app.route("/magic-folder/<string:folder_name>/file-status", methods=['GET'])
    def folder_file_status(self, request, folder_name):
        """
        Render status information for every file in a given folder
        """
        _application_json(request)  # set reply headers
        folder_config = self._global_config.get_magic_folder(folder_name)

        return json.dumps([
            {
                "relpath": relpath,
                "mtime": ns_to_seconds(ps.mtime_ns),
                "last-updated": ns_to_seconds(last_updated_ns),
                "last-upload-duration": float(upload_duration_ns) / 1000000000.0 if upload_duration_ns else None,
                "size": ps.size,
            }
            for relpath, ps, last_updated_ns, upload_duration_ns
            in folder_config.get_all_current_snapshot_pathstates()
        ]).encode("utf8")

    @app.route("/magic-folder/<string:folder_name>/conflicts", methods=['GET'])
    def list_conflicts(self, request, folder_name):
        """
        Render information about all known conflicts in a given folder
        """
        _application_json(request)  # set reply headers
        folder_config = self._global_config.get_magic_folder(folder_name)
        return json.dumps({
            relpath: [
                conflict.author_name
                for conflict in conflicts
            ]
            for relpath, conflicts in folder_config.list_conflicts().items()
        }).encode("utf8")

    @app.route("/magic-folder/<string:folder_name>/tahoe-objects", methods=['GET'])
    def folder_tahoe_objects(self, request, folder_name):
        """
        Renders a list of all the object-sizes of all Tahoe objects a
        given magic-folder currently cares about. This is, for each
        Snapshot: the Snapshot capability, the metadata capability and
        the content capability.
        """
        _application_json(request)  # set reply headers
        folder_config = self._global_config.get_magic_folder(folder_name)
        sizes = folder_config.get_tahoe_object_sizes()
        return json.dumps(sizes).encode("utf8")


class _InputError(APIError):
    """
    Local errors with our input validation to report back to HTTP
    clients.
    """
    def __init__(self, reason):
        super(_InputError, self).__init__(code=http.BAD_REQUEST, reason=reason)

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
        yield snapshot_path, _list_all_path_snapshots(folder_config, snapshot_path)


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
        u"identifier": str(snapshot.identifier),
        u"author": snapshot.author.to_remote_author().to_json(),
    }
    return result


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


def magic_folder_web_service(web_endpoint, global_config, global_service, get_auth_token, status_service):
    """
    :param web_endpoint: a IStreamServerEndpoint where we should listen

    :param GlobalConfigDatabase global_config: A reference to the shared magic
        folder state defining the service.

    :param get_auth_token: a callable that returns the current authentication token

    :param IStatus status_service: our status reporting service

    :returns: a StreamServerEndpointService instance
    """
    v1_resource = APIv1(global_config, global_service, status_service).app.resource()
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
    expected = b"Bearer " + auth_token
    # This ends up calling `hmac.compare_digest`. Looking at the source for
    # that method suggests that it tries to make timing dependence for unequal
    # length strings be on the second argument.
    # Pass the attacker controlled value, to avoid leaking length information
    # of the expected value.
    return timing_safe_compare(
        expected,
        authorization[0].encode("ascii"),
    )
