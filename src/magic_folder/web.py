from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)


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

from eliot import write_failure
from eliot.twisted import inline_callbacks

from twisted.python.filepath import (
    FilePath,
)
from twisted.internet.defer import (
    inlineCallbacks,
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

from hyperlink import DecodedURL
from werkzeug.exceptions import HTTPException
from werkzeug.routing import RequestRedirect

from klein import Klein

from cryptography.hazmat.primitives.constant_time import bytes_eq as timing_safe_compare

from .common import APIError
from .invite import (
    accept_invite,
    InviteError,
)
from .status import (
    StatusFactory,
    IStatus,
)
from .snapshot import (
    create_author,
)
from .util.capabilities import (
    is_readonly_directory_cap,
)
from .util.file import (
    ns_to_seconds,
)


def _ensure_utf8_bytes(value):
    if isinstance(value, unicode):
        return value.encode('utf-8')
    return value


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
        return json.dumps(exc.to_json())

    @app.handle_errors(RequestRedirect)
    def handle_redirect(self, request, failure):
        exc = failure.value
        request.setResponseCode(exc.code, exc.name)
        # Werkzeug double encodes the path of the redirect URL, when merging
        # slashes[1]. It does not double encode the path when:
        # - collapsing trailing slashes
        # - redirecting aliases to the cannonical URL
        # - an explicit redirect_to on a URL
        # Since we don't have rules that trigger the second cases[2],
        # we can work around this be decoding the path here.
        # [1] https://github.com/pallets/werkzeug/issues/2157
        # [2] checked in magic_folder.test.test_web.RedirectTests.test_werkzeug_issue_2157_fix
        location = DecodedURL.from_text(exc.new_url.decode('utf-8'))
        location = location.encoded_url.replace(path=location.path).to_text()
        request.setHeader("location", location)
        _application_json(request)
        return json.dumps({"location": location})

    @app.handle_errors(HTTPException)
    def handle_http_error(self, request, failure):
        """
        Convert a werkzeug py:`HTTPException` to a json response.

        This mirrors the default code in klein for handling these
        exceptions, but generates a json body.
        """
        exc = failure.value
        request.setResponseCode(exc.code, exc.name)
        resp = exc.get_response({})
        for header, value in resp.headers.items(lower=True):
            # We skip these, since we have our own content.
            if header in ("content-length", "content-type"):
                continue
            request.setHeader(
                _ensure_utf8_bytes(header), _ensure_utf8_bytes(value)
            )
        _application_json(request)
        return json.dumps({"reason": exc.description})

    @app.handle_errors(Exception)
    def fallback_error(self, request, failure):
        """
        Turn unknown exceptions into 500 errors, and log the failure.
        """
        write_failure(failure)
        request.setResponseCode(http.INTERNAL_SERVER_ERROR)
        _application_json(request)
        print(failure)
        return json.dumps({"reason": "unexpected error processing request"})

    @app.route("/status")
    def status(self, request):
        return WebSocketResource(StatusFactory(self._status_service))

    @app.route("/magic-folder", methods=["POST"])
    @inlineCallbacks
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
    @inlineCallbacks
    def list_participants(self, request, folder_name):
        """
        List all participants of this folder
        """
        folder_service = self._global_service.get_folder_service(folder_name)

        participants = yield folder_service.participants()

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
        returnValue(json.dumps(reply).encode("utf8"))

    @app.route("/magic-folder/<string:folder_name>/participants", methods=['POST'])
    @inlineCallbacks
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

        personal_dmd_cap = participant["personal_dmd"]
        if not is_readonly_directory_cap(personal_dmd_cap):
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
        return json.dumps(dict(_list_all_snapshots(self._global_config)))

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
    @inlineCallbacks
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
                u"scan_interval": mf.scan_interval,
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
        ])

    @app.handle_errors(InviteError)
    def handle_invite_error(self, request, failure):
        exc = failure.value
        request.setResponseCode(exc.code or http.NOT_ACCEPTABLE)
        _application_json(request)
        return json.dumps(exc.to_json())

    @app.route("/magic-folder/<string:folder_name>/invite", methods=['POST'])
    @inline_callbacks
    def create_invite(self, request, folder_name):
        """
        Create a new invite for a given folder.

        The body of the request must be a JSON dict that has the
        following keys:

        - "suggested-petname": arbitrary, valid author name
        """
        body = _load_json(request.content.read())
        if set(body.keys()) != {u"suggested-petname"}:
            raise _InputError(
                u'Body must be {"suggested-petname": "..."}'
            )
        author_name = body[u"suggested-petname"]
        folder_service = self._global_service.get_folder_service(folder_name)
        folder_config = folder_service.config

        from twisted.internet import reactor
        invite = yield folder_service.invite_manager.create_invite(reactor, author_name, folder_config)
        yield invite.await_code()
        returnValue(json.dumps(invite.marshal()))

    @app.route("/magic-folder/<string:folder_name>/invite-wait", methods=['POST'])
    @inline_callbacks
    def await_invite(self, request, folder_name):
        """
        Await acceptance of a given invite.

        The body of the request must be a JSON dict that has the
        following keys:

        - "id": the ID of an existing invite
        """
        body = _load_json(request.content.read())
        if set(body.keys()) != {u"id"}:
            raise _InputError(
                u'Body must be {"id": "..."}'
            )
        folder_service = self._global_service.get_folder_service(folder_name)
        invite = folder_service.invite_manager.get_invite(body[u"id"])
        yield invite.await_done()
        if invite.is_accepted():
            request.setResponseCode(http.OK)
            request.write(json.dumps(invite.marshal()))
            return
        else:
            raise APIError(
                code=400,
                reason="Wormhole failed or other side declined",
            )

    @app.route("/magic-folder/<string:folder_name>/join", methods=['POST'])
    @inline_callbacks
    def accept_invite(self, request, folder_name):
        """
        Accept an invite and create a new folder.

        The body of the request must be a JSON dict that has the
        following keys:

        - "name": arbitrary, valid magic folder name
        - "invite-code": wormhole code
        - "local-directory": absolute path of an existing local directory to synchronize files in
        - "author": arbitrary, valid author name
        - "poll-interval": seconds between remote update checks
        - "scan-interval": seconds between local update checks
        """
        body = _load_json(request.content.read())
        required_keys = {
            u"name",
            u"invite-code",
            u"local-directory",
            u"author",
            u"poll-interval",
            u"scan-interval",
        }
        if required_keys != set(body.keys()):
            missing = required_keys - set(body.keys())
            if missing:
                raise _InputError(
                    "Missing keys: {}".format(" ".join(missing))
                )
            raise _InputError(
                "Extra keys: {}".format(" ".join(missing))
            )
        folder_name = body["name"].decode("utf8")
        wormhole_code = body["invite-code"].decode("utf-8")
        author_name = body["author"].decode("utf-8")
        local_dir = FilePath(body["local-directory"].decode("utf-8"))
        poll_interval = int(body["poll-interval"])
        scan_interval = int(body["scan-interval"])

        _application_json(request)

        # create a folder via wormhole
        from twisted.internet import reactor
        try:
            yield accept_invite(
                reactor, self._global_config, wormhole_code, folder_name,
                author_name, local_dir, poll_interval, scan_interval,
                self._tahoe_client,
            )
        except ValueError as e:
            raise _InputError(str(e))

        # start the services for this folder
        mf = self._global_service._add_service_for_folder(folder_name)
        yield mf.ready()

        request.setResponseCode(http.CREATED)
        request.write(b"{}")
        request.finish()

    @app.route("/magic-folder/<string:folder_name>/invites", methods=['GET'])
    def list_invites(self, request, folder_name):
        """
        List pending invites.
        """
        _application_json(request)
        folder_service = self._global_service.get_folder_service(folder_name)
        request.setResponseCode(http.CREATED)
        request.write(
            json.dumps(
                folder_service.invite_manager.list_invites()
            )
        )
        request.finish()

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
        })

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
        return json.dumps(sizes)


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
        u"identifier": unicode(snapshot.identifier),
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
    expected = b"Bearer %s" % (auth_token,)
    # This ends up calling `hmac.compare_digest`. Looking at the source for
    # that method suggests that it tries to make timing dependence for unequal
    # length strings be on the second argument.
    # Pass the attacker controlled value, to avoid leaking length information
    # of the expected value.
    return timing_safe_compare(
        expected,
        authorization[0].encode("ascii"),
    )
