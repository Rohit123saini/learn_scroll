"""
post/audience.py  (P1)

Single source of truth for "can this viewer see this post?" for the four
composer audiences:

  public         anyone
  connections    accepted followers of the author          ("Followers")
  close_friends  people on the author's CloseFriend list   (same rule as stories)
  class_campus   people who share a classroom or a campus with the author
  private        only the author

Anything other than `public` never appears in hashtag / explore / reels
discovery (those queries already filter `visibility='public'`). A post the
viewer may not see is reported as NOT FOUND (404), never 403, so its
existence is not leaked.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


def _follows(viewer, author_id):
    from user_profile.models import Follow
    return Follow.objects.filter(
        follower=viewer, following_id=author_id, status=Follow.Status.ACCEPTED,
    ).exists()


def _on_close_friends(viewer, author_id):
    from .models import CloseFriend
    return CloseFriend.objects.filter(owner_id=author_id, friend=viewer).exists()


def campus_ids_of(user):
    """Campuses a user belongs to (active student enrollment or active staff row)."""
    try:
        from campus.models import StaffProfile, StudentEnrollment
    except Exception:  # campus app not installed
        return set()
    ids = set(
        StudentEnrollment.objects.filter(student=user, status=StudentEnrollment.Status.ACTIVE)
        .values_list('section__school_class__campus_id', flat=True)
    )
    ids |= set(
        StaffProfile.objects.filter(user=user, is_active=True).values_list('campus_id', flat=True)
    )
    ids.discard(None)
    return ids


def classroom_ids_of(user):
    """Tuition classrooms a user teaches or holds a successful active pass for."""
    try:
        from tuitionclass.models import Classroom, PassPurchase
    except Exception:
        return set()
    ids = set(Classroom.objects.filter(teacher=user).values_list('id', flat=True))
    ids |= set(
        PassPurchase.objects.filter(
            student=user, status=PassPurchase.Status.SUCCESS, is_active=True,
        ).values_list('class_pass__classroom_id', flat=True)
    )
    return ids


def classroom_member_ids(classroom_id):
    """X5 (4): user ids that belong to ONE tuition classroom - its teacher plus every student with a successful, active
    pass. Same membership rule as [classroom_ids_of] (that one answers "which classes is THIS user in", this one
    answers "who is in THIS class"). Empty set when the class does not exist / tuitionclass is not importable."""
    try:
        from tuitionclass.models import Classroom, PassPurchase
    except Exception:
        return set()
    try:
        cid = int(classroom_id)
    except (TypeError, ValueError):
        return set()
    ids = set(Classroom.objects.filter(id=cid).values_list('teacher_id', flat=True))
    ids |= set(
        PassPurchase.objects.filter(
            class_pass__classroom_id=cid, status=PassPurchase.Status.SUCCESS, is_active=True,
        ).values_list('student_id', flat=True)
    )
    ids.discard(None)
    return ids


# ---------------------------------------------------------------------------------------------------------------------
# X12 - "Save as lecture / reel / post" from a class recording. A post made that way carries
# `metadata['class_recording'] = {'classroom_id': <int>, 'session_id': <int>}` and, unless the teacher chose otherwise,
# visibility `class_campus`. Plain `class_campus` means "anyone who shares ANY classroom or campus with the author" -
# far too wide for a paid class: a student of the teacher's OTHER class would see this class's lecture. A post with that
# marker is therefore visible only to the members of THAT classroom (its teacher, its staff, students with an active pass).
# ---------------------------------------------------------------------------------------------------------------------
def class_recording_classroom_id(post):
    """The classroom a class-recording post is locked to (int), or None for an ordinary post."""
    meta = getattr(post, 'metadata', None)
    if not isinstance(meta, dict):
        return None
    info = meta.get('class_recording')
    if not isinstance(info, dict):
        return None
    try:
        return int(info.get('classroom_id'))
    except (TypeError, ValueError):
        return None


CLASS_RECORDING_NARROW_VISIBILITIES = ('class_campus', 'private')


def class_recording_widen_error(post, user, new_visibility):
    """Message when `user` may NOT move a class-recording post to `new_visibility`, else None (X12).

    A post saved from a class recording is locked to its classroom (metadata.class_recording). Only the classroom's own
    teacher may take it outside the class (public / followers / close friends) - the same rule recording_save applies when
    the clip is made - so a co-teacher / moderator who saved it cannot widen it later through an edit or the visibility
    endpoint. Narrowing (class only, private) is always allowed. A post without the marker is never affected."""
    classroom_id = class_recording_classroom_id(post)
    if classroom_id is None or new_visibility in CLASS_RECORDING_NARROW_VISIBILITIES:
        return None
    try:
        from tuitionclass.models import Classroom
        teacher_id = Classroom.objects.filter(pk=classroom_id).values_list('teacher_id', flat=True).first()
    except Exception:
        teacher_id = None
    if teacher_id is not None and teacher_id == getattr(user, 'id', None):
        return None
    return "Only the class teacher can share a class recording outside the class."


def classroom_staff_ids(classroom_id):
    """User ids of a classroom's staff rows (co-teacher / moderator / TA)."""
    try:
        from tuitionclass.models import ClassroomStaff
    except Exception:
        return set()
    return set(ClassroomStaff.objects.filter(classroom_id=classroom_id).values_list('user_id', flat=True))


def class_gate_classroom_ids(user):
    """Classrooms whose class-only recordings `user` may open: the ones in [classroom_ids_of] plus the ones they staff."""
    ids = set(classroom_ids_of(user))
    try:
        from tuitionclass.models import ClassroomStaff
        ids |= set(ClassroomStaff.objects.filter(user=user).values_list('classroom_id', flat=True))
    except Exception:
        pass
    ids.discard(None)
    return ids


def shares_class_or_campus(viewer, author):
    if viewer.id == author.id:
        return True
    if campus_ids_of(viewer) & campus_ids_of(author):
        return True
    if classroom_ids_of(viewer) & classroom_ids_of(author):
        return True
    return False


def can_view_post(post, viewer):
    if viewer is None or not getattr(viewer, 'is_authenticated', False):
        return post.visibility == 'public'
    if post.user_id == viewer.id:
        return True
    vis = post.visibility
    if vis == 'public':
        return True
    if vis == 'connections':
        return _follows(viewer, post.user_id)
    if vis == 'close_friends':
        return _on_close_friends(viewer, post.user_id)
    # round 5: an accepted co-author sees the post whatever its audience (except a private one is never co-authored)
    from .models import PostCollaborator
    if vis != 'private' and PostCollaborator.objects.filter(
            post=post, user=viewer, status=PostCollaborator.STATUS_ACCEPTED).exists():
        return True
    if vis == 'class_campus':
        gate = class_recording_classroom_id(post)
        if gate is not None:                       # X12: locked to ONE classroom, not to the author's whole circle
            return viewer.id in classroom_member_ids(gate) or viewer.id in classroom_staff_ids(gate)
        return shares_class_or_campus(viewer, post.user)
    return False  # private / unknown


def filter_posts_visible_to(queryset, viewer):
    """Narrow a Post queryset to what `viewer` may see. Computes the viewer's
    own campus/classroom/follow/close-friend sets once (not per row)."""
    from user_profile.models import Follow
    from .models import CloseFriend

    following = Follow.objects.filter(
        follower=viewer, status=Follow.Status.ACCEPTED).values_list('following_id', flat=True)
    owners_with_me = CloseFriend.objects.filter(friend=viewer).values_list('owner_id', flat=True)

    q = Q(visibility='public') | Q(user=viewer)
    # round 5: posts I was invited to co-author and accepted
    from .models import PostCollaborator
    q |= Q(pk__in=PostCollaborator.objects.filter(
        user=viewer, status=PostCollaborator.STATUS_ACCEPTED).exclude(post__visibility='private').values('post_id'))
    q |= Q(visibility='connections', user_id__in=list(following))
    q |= Q(visibility='close_friends', user_id__in=list(owners_with_me))

    my_campuses = campus_ids_of(viewer)
    my_rooms = classroom_ids_of(viewer)
    if my_campuses or my_rooms:
        peers = set()
        try:
            from campus.models import StaffProfile, StudentEnrollment
            if my_campuses:
                peers |= set(StudentEnrollment.objects.filter(
                    status=StudentEnrollment.Status.ACTIVE,
                    section__school_class__campus_id__in=my_campuses,
                ).values_list('student_id', flat=True))
                peers |= set(StaffProfile.objects.filter(
                    is_active=True, campus_id__in=my_campuses).values_list('user_id', flat=True))
        except Exception:
            pass
        try:
            from tuitionclass.models import Classroom, PassPurchase
            if my_rooms:
                peers |= set(PassPurchase.objects.filter(
                    status=PassPurchase.Status.SUCCESS, is_active=True,
                    class_pass__classroom_id__in=my_rooms,
                ).values_list('student_id', flat=True))
                peers |= set(Classroom.objects.filter(id__in=my_rooms).values_list('teacher_id', flat=True))
        except Exception:
            pass
        # X12: a class-recording post (metadata.class_recording) is NOT part of the author's wide circle - see above.
        q |= Q(visibility='class_campus', user_id__in=list(peers)) & ~Q(metadata__has_key='class_recording')
    gate_ids = class_gate_classroom_ids(viewer)
    if gate_ids:
        q |= Q(visibility='class_campus', metadata__class_recording__classroom_id__in=sorted(gate_ids))
    return queryset.filter(q)
