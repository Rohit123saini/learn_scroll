"""API versioning: `/v1/<anything>` is accepted as an alias of `/<anything>`.

The prefix is stripped BEFORE URL resolving, so no urls.py changes are needed and every
existing URL keeps working. `request.api_version` = 1 for `/v1/...`, 0 for unversioned URLs.
Every response also carries an `X-API-Version` header.

This is a self-contained version: it has no dependency on core.app_config, so it can be
dropped into an older backend as-is.
"""

CURRENT_API_VERSION = 1
_PREFIX = "/v1/"


def strip_version_prefix(path: str):
    """'/v1/post/feed/' -> ('/post/feed/', 1); anything else -> (path, 0)."""
    if path.startswith(_PREFIX):
        return path[len(_PREFIX) - 1:], CURRENT_API_VERSION
    return path, 0


class ApiVersionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        original = request.path_info
        new_path, version = strip_version_prefix(original)
        if version:
            request.path = request.path[: len(request.path) - len(original)] + new_path
            request.path_info = new_path
        request.api_version = version

        response = self.get_response(request)
        response["X-API-Version"] = str(CURRENT_API_VERSION)
        return response
