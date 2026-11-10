"""
post/content_defaults.py  (S4b -- account-level defaults for new posts / stories)

The person chose, in Settings -> Privacy, what their NEXT post / story should start with (audience, who may comment,
allow reshare / save). This module turns that into the values a create path fills in. The rules themselves (levels,
the minor floor, caching) live in `user_profile/privacy.py::content_defaults_for`; nothing is duplicated here.

    story_fill(user, data)          -> {"audience"?, "allow_reshare"?, "allow_save"?}
    post_fill(user, data)           -> {"visibility"?, "comments_limit"?, "allow_reshare"?, "allow_save"?}
    post_extra_fill(user, data)     -> {"comments_limit"?, "allow_reshare"?, "allow_save"?}   (paths with no audience)
    post_visibility_default(user, sent)  -> the audience to store when `sent` is empty

Golden rule: a key the client SENT is never touched ("explicit value wins"). `data` is the raw request data
(`request.data` / `serializer.initial_data`), not the validated attrs: a DRF BooleanField reads "missing" as False on a
multipart form, so only the raw data can tell "the app left it out" from "the app turned it off".

Fail-safe: if the defaults cannot be read (cache / DB hiccup) the create path keeps the model defaults and the post
still publishes; the problem is logged. Existing posts / stories are never rewritten.
"""
import logging

logger = logging.getLogger(__name__)


def _sent(data, key) -> bool:
    try:
        return data is not None and key in data
    except TypeError:
        return False


def _defaults(user) -> dict:
    try:
        from user_profile import privacy

        return privacy.content_defaults_for(user)
    except Exception:  # never block a publish because the defaults could not be read
        logger.warning("content defaults unavailable (user=%s)", getattr(user, "pk", None), exc_info=True)
        return {}


def story_fill(user, data) -> dict:
    d = _defaults(user)
    out = {}
    if d:
        if not _sent(data, "audience") and d.get("story_audience"):
            out["audience"] = d["story_audience"]
        if not _sent(data, "allow_reshare"):
            out["allow_reshare"] = bool(d.get("allow_reshare", True))
        if not _sent(data, "allow_save"):
            out["allow_save"] = bool(d.get("allow_save", True))
    return out


def post_extra_fill(user, data) -> dict:
    d = _defaults(user)
    out = {}
    if d:
        if not _sent(data, "comments_limit") and d.get("comments_limit"):
            out["comments_limit"] = d["comments_limit"]
        if not _sent(data, "allow_reshare"):
            out["allow_reshare"] = bool(d.get("allow_reshare", True))
        if not _sent(data, "allow_save"):
            out["allow_save"] = bool(d.get("allow_save", True))
    return out


def post_fill(user, data) -> dict:
    out = post_extra_fill(user, data)
    if not _sent(data, "visibility"):
        d = _defaults(user)
        if d.get("post_visibility"):
            out["visibility"] = d["post_visibility"]
    return out


def post_visibility_default(user, sent) -> str:
    """Audience for a create path that reads `visibility` itself (chunked init, resource post)."""
    if sent:
        return sent
    return _defaults(user).get("post_visibility") or "public"
