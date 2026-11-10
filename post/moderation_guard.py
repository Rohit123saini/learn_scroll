"""
post/moderation_guard.py - ONE rule for "may this viewer touch this post at all?".

Feeds already only list `moderation_status='approved'` posts, but single-post
endpoints (detail by id, react, comment, share, save, answer, poll) never looked at
it - so a post taken down for copyright (or flagged by reports) was still fully
reachable through a direct link, a share or a saved list. Every one of those
endpoints now asks `post_hidden_for(viewer, post)` and answers 404 exactly as for a
post that doesn't exist.

Hidden = deleted, archived by its author, still scheduled (not yet published), or not approved (pending / rejected / flagged / copyright_hold /
copyright_removed / legal_removed) - except to the post's own author and to staff.
"""

LOCKED_BY_COPYRIGHT = ("copyright_hold", "copyright_removed", "legal_removed")


def post_hidden_for(viewer, post) -> bool:
    if post is None or post.is_deleted:
        return True
    if getattr(post, "is_archived", False):
        # Archived = hidden from everyone but the author (who restores it) and staff.
        return not (viewer is not None and getattr(viewer, "is_authenticated", False)
                    and (viewer.id == post.user_id or getattr(viewer, "is_staff", False)))
    if post.moderation_status == "approved" and not getattr(post, "is_scheduled", False):
        return False
    if viewer is not None and getattr(viewer, "is_authenticated", False):
        if viewer.id == post.user_id or getattr(viewer, "is_staff", False):
            return False
    return True


def post_locked_by_copyright(post) -> bool:
    """Owner may not edit / re-publish a post while a copyright claim holds it."""
    return post.moderation_status in LOCKED_BY_COPYRIGHT
