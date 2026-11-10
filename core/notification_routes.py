"""
core/notification_routes.py  -  N2: ONE place that says "tap on this NotifType -> which screen".

Kyu: tap par galat / koi screen na khule to user ka bharosa tootta hai. Pehle routing 3 jagah alag-alag thi
(Flutter `notifications_screen.dart`, `services/push_router.dart`, aur kuch types kahin nahi). Ab:

  * Backend (yeh file)  = source of truth. Har `Notification.NotifType` ka ek EXPLICIT `RouteSpec` (SPECS) - ek
    naya NotifType add karo aur spec na likho to `core/tests_notification_routes.py` fail hota hai.
  * `route_for()`       = ek notification row / push ke liye `{route, target_type, target_id}` (serializer +
    `build_push_payload` me `route` key ke roop me jaata hai, naye app-version ke bina bhi naye type ko route karne ke liye).
  * Flutter             = `tool/gen_notification_routes.py` isi table se `notification_route_table.g.dart` +
    `notification_route_cases.json` banata hai; `NotificationRouter` (Flutter) usi table ko chalata hai. Python aur
    Dart dono wahi JSON cases check karte hain, isliye dono side ka route same rehta hai.

Rules:
  * Pure Python - Django import NAHI (generator script isse file-path se load karta hai).
  * `route` ke naam Flutter ke `PushDestination` enum ke naam hain (Python test dono ko compare karta hai).
  * Rule order = priority (pehla match jeet-ta hai). Koi rule match na kare to `RouteSpec.fallback`.
  * `unavailable` = "Ye content ab available nahi" screen: type ko ek khaas target chahiye (post / class / chat...)
    aur woh id payload me nahi hai (target delete ho gaya, SET_NULL, purani row). `notifications` = row khud dikhao
    (dialog / list), koi alag screen nahi. Crash / dead tap kabhi nahi.
"""
from __future__ import annotations

from typing import Iterable, NamedTuple

# --------------------------------------------------------------------------------------------------------------------
# Destinations. route -> (target_type, args). `args` = payload keys the screen needs (FIRST key = target_id).
# Flutter enum `PushDestination` must have exactly these names (+ nothing else) - tested.
# --------------------------------------------------------------------------------------------------------------------
ROUTES: dict[str, tuple[str, tuple[str, ...]]] = {
    "chat": ("conversation", ("conversation_id",)),
    "doubt": ("doubt", ("doubt_id", "group_id", "conversation_id")),
    "followRequests": ("screen", ()),
    "profile": ("user", ("actor_username", "actor_id")),
    "post": ("post", ("post_id", "comment_id")),
    "story": ("story", ("story_id",)),
    "reel": ("post", ("post_id",)),
    "assignments": ("screen", ()),
    "testSeries": ("test_series", ("series_id",)),
    "classroom": ("classroom", ("classroom_id",)),
    "classroomTests": ("classroom", ("classroom_id",)),
    "campus": ("screen", ()),
    "campusLive": ("live_session", ("live_session_id",)),
    "homeworkDiary": ("section", ("section_id", "campus_id")),
    "classResources": ("folder", ("folder_id", "section_id", "campus_id")),
    "campusRecording": ("recording", ("recording_id", "live_session_id", "section_id", "subject_id", "campus_id")),
    "campusNotes": ("section", ("section_id", "subject_id", "campus_id")),
    "parentChat": ("thread", ("thread_id",)),
    "parentChatList": ("screen", ("campus_id",)),
    "ptm": ("ptm_event", ("event_id", "campus_id", "student_id")),
    "admissions": ("screen", ("campus_id",)),
    "parentChild": ("student", ("student_id", "campus_id")),
    "parentWeekly": ("screen", ("campus_id",)),  # P-F14: the parent weekly report screen
    "staffLeave": ("leave", ("leave_id", "campus_id")),
    "wallet": ("screen", ()),
    "sessions": ("screen", ()),
    "supportTicket": ("ticket", ("ticket_id",)),
    "notifications": ("screen", ()),
    "creatorInsights": ("screen", ()),
    "coAuthorInvites": ("post", ("post_id",)),
    "copyrightComplaints": ("copyright_claim", ("claim_id",)),
    "copyrightStrikes": ("copyright_claim", ("claim_id",)),
    "weeklyRecap": ("screen", ()),
    "referrals": ("screen", ()),
    "parentAccess": ("screen", ()),
    "certificates": ("screen", ()),
    "home": ("screen", ()),
    "none": ("none", ()),
    "unavailable": ("none", ()),
}


def _present(v) -> bool:
    return v is not None and str(v).strip() not in ("", "None", "null")


def _truthy(v) -> bool:
    if v is True:
        return True
    return str(v).strip().lower() in ("true", "1")


class Rule(NamedTuple):
    route: str
    requires: tuple = ()  # every key must be present (non-empty)
    eq: tuple = ()  # ((key, value), ...) str(data[key]) must equal value
    ne: tuple = ()  # ((key, value), ...) str(data[key]) must NOT equal value (missing key passes)
    truthy: tuple = ()  # keys that must be true / "true" / "1" (a push turns bools into strings)
    in_app: bool = False  # rule only applies to a tap inside the in-app notification list (not a push tap)

    def matches(self, d: dict, in_app: bool = False) -> bool:
        if self.in_app and not in_app:
            return False
        if not all(_present(d.get(k)) for k in self.requires):
            return False
        if any(str(d.get(k, "")).strip() != v for k, v in self.eq):
            return False
        if any(str(d.get(k, "")).strip() == v for k, v in self.ne):
            return False
        return all(_truthy(d.get(k)) for k in self.truthy)


class RouteSpec(NamedTuple):
    rules: tuple
    fallback: str = "unavailable"


def R(route, *requires, eq=None, ne=None, truthy=(), in_app=False) -> Rule:
    return Rule(
        route,
        tuple(requires),
        tuple(sorted((eq or {}).items())),
        tuple(sorted((ne or {}).items())),
        tuple(truthy),
        in_app,
    )


def S(*rules: Rule, fallback: str = "unavailable") -> RouteSpec:
    return RouteSpec(tuple(rules), fallback)


# ---- reusable specs -------------------------------------------------------------------------------------------------
_CLASSROOM = S(R("classroom", "classroom_id"))  # tuitionclass rows: classroom FK -> data.classroom_id (SET_NULL => gone)
_WALLET = S(R("wallet"), fallback="wallet")
_CAMPUS_HUB = S(R("campus"), fallback="campus")
_ASSIGN = S(R("assignments"), fallback="assignments")
_PARENT_CHILD = S(R("parentChild", "student_id"), R("campus"), fallback="campus")  # parent push about ONE child
_SHOW_ROW = S(R("notifications"), fallback="notifications")  # nothing to open - the row itself is the content
_HOME = S(R("home"), fallback="home")
_SERIES = S(R("testSeries", "series_id"))
_POST = S(R("post", "post_id"))
_CHAT = S(R("chat", "conversation_id"))

# ---- the table: EVERY Notification.NotifType value -------------------------------------------------------------------
SPECS: dict[str, RouteSpec] = {
    # tuitionclass (classroom FK)
    "join_request_received": _CLASSROOM,
    "join_request_accepted": _CLASSROOM,
    "join_request_rejected": _CLASSROOM,
    "session_reminder": _CLASSROOM,
    "session_live": _CLASSROOM,
    "session_cancelled": _CLASSROOM,
    "waitlist_promoted": _CLASSROOM,
    "classroom_flagged": _CLASSROOM,
    "staff_added": _CLASSROOM,
    "review_posted": _CLASSROOM,
    "classroom_shared": _CLASSROOM,
    "report_reviewed": _CLASSROOM,
    "classroom_created_by_followed": _CLASSROOM,
    "query_answered": S(R("post", "post_id"), R("classroom", "classroom_id")),  # post doubt answer | class doubt
    "certificate_issued": S(
        R("testSeries", "series_id"), R("classroom", "classroom_id"), R("certificates"), fallback="certificates"
    ),
    "notice_posted": S(R("classroom", "classroom_id"), R("campus"), fallback="campus"),  # class notice | campus notice
    # assignments / tests
    "assigments_graded": _ASSIGN,
    "assigments_posted": _ASSIGN,
    "submission_received": _ASSIGN,
    "assigments_posted_campus": _ASSIGN,
    "assigments_due_reminder": _ASSIGN,
    "assigments_due_soon": _ASSIGN,
    "staff_assigments_approved": _ASSIGN,
    "staff_assigments_rejected": _ASSIGN,
    "testseries_posted": S(R("classroomTests", "classroom_id"), R("testSeries", "series_id")),
    "testseries_checked": _SERIES,
    "testseries_due_soon": _SERIES,  # N6 - test window closing in 24h / 1h
    "testseries_review_received": _SERIES,
    "testseries_query_received": _SERIES,
    "testseries_query_answered": _SERIES,
    "testseries_created_by_followed": _SERIES,
    "testseries_payout_released": S(R("testSeries", "series_id"), R("wallet"), fallback="wallet"),
    # wallet / passes
    "pass_refunded": _WALLET,
    "pass_gift_received": _WALLET,
    "pass_gift_claimed": _WALLET,
    "pass_auto_renewed": _WALLET,
    "new_device_login": S(R("sessions"), fallback="sessions"),  # R5 - "Log out this device" list (Security > Devices)
    "purchase_receipt": _WALLET,  # R9 - coin top-up / pass purchase receipt (wallet shows the receipt + PDF)
    "auto_renew_failed": _WALLET,
    "pass_gift_expired": _WALLET,
    "withdrawal_approved": _WALLET,
    "withdrawal_rejected": _WALLET,
    "withdrawal_paid": _WALLET,
    # generic rows carry `data.kind` (video_processing_failed / referral_commission / moderation_notice / ...)
    "generic": S(
        R("reel", "post_id", eq={"kind": "video_processing_failed"}),
        R("wallet", eq={"kind": "referral_commission"}),
        R("notifications"),
        fallback="notifications",
    ),
    # copyrights (data.claim_id; the screens list the user's own notices / strikes)
    "copyright_notice": S(R("copyrightComplaints"), fallback="copyrightComplaints"),
    "copyright_decision": S(R("copyrightComplaints"), fallback="copyrightComplaints"),
    "copyright_standing": S(R("copyrightStrikes"), fallback="copyrightStrikes"),
    "copyright_staff": _SHOW_ROW,
    # message app
    "chat_message": _CHAT,
    "mention": _CHAT,
    "incoming_call": _CHAT,
    "parent_device_pending": S(R("parentAccess"), fallback="parentAccess"),
    "doubt_answered": S(R("doubt", "group_id", "conversation_id"), R("chat", "conversation_id")),
    # posts / stories / social
    "post_liked": _POST,
    "post_commented": _POST,
    "post_reposted": _POST,
    "new_post_from_followed": _POST,
    "feed_doubt_for_teacher": _POST,
    "post_tag": S(R("coAuthorInvites", truthy=("collab_invite",)), R("post", "post_id")),  # collab invite | plain tag
    "story_mention": S(R("story", "story_id")),
    "story_reaction": S(R("story", "story_id")),
    "follow_request_received": S(R("profile", "actor_username", in_app=True), R("followRequests"), fallback="followRequests"),
    "follow_request_accepted": S(R("profile", "actor_username")),
    "creator_feedback": S(R("creatorInsights"), fallback="creatorInsights"),
    # growth
    "streak_milestone_reached": _HOME,
    "streak_at_risk": _HOME,
    "study_reminder": _HOME,  # S8: the user's own "time to study" reminder - lands on Home (continue learning)
    "revision_deck_reminder": _HOME,  # P-F3_F4: "N items to revise" - Home habit card has the Revise button
    "weekly_recap_ready": S(R("weeklyRecap"), fallback="weeklyRecap"),
    "referral_reward": S(R("referrals"), fallback="referrals"),
    "support_reply": S(R("supportTicket", "ticket_id"), R("notifications"), fallback="notifications"),
    "report_outcome": _SHOW_ROW,  # the reported content may be hidden now - never opened
    "content_digest": _SHOW_ROW,  # the in-app row opens its items; a push lands on the list
    # campus
    "campus_session_scheduled": _CAMPUS_HUB,
    "campus_session_live": S(R("campusLive", "live_session_id", ne={"mode": "offline"}), R("campus"), fallback="campus"),
    "campus_class_starting": _CAMPUS_HUB,
    "low_attendance_alert": _CAMPUS_HUB,
    "campus_reward_earned": _CAMPUS_HUB,
    "campus_doubt_posted": _CAMPUS_HUB,
    "campus_doubt_replied": _CAMPUS_HUB,
    "campus_substitute_assigned": _CAMPUS_HUB,
    "campus_class_substituted": _CAMPUS_HUB,
    "campus_substitute_cancelled": _CAMPUS_HUB,
    "campus_emergency": _CAMPUS_HUB,
    "campus_safety": _CAMPUS_HUB,
    "result_published": _PARENT_CHILD,
    "fee_due_reminder": _PARENT_CHILD,
    "fee_overdue_reminder": _PARENT_CHILD,
    "campus_fee_paid": _PARENT_CHILD,
    "campus_student_absent": _PARENT_CHILD,
    "campus_parent_weekly": S(R("parentWeekly"), fallback="parentWeekly"),  # P-F14
    "homework_diary_posted": S(R("homeworkDiary", "campus_id", "section_id"), R("campus"), fallback="campus"),
    "homework_diary_due": S(R("homeworkDiary", "campus_id", "section_id"), R("campus"), fallback="campus"),
    "campus_leave_requested": S(R("staffLeave", "campus_id"), R("campus"), fallback="campus"),
    "campus_leave_decided": S(R("staffLeave", "campus_id"), R("campus"), fallback="campus"),
    "campus_ptm_scheduled": S(R("ptm", "event_id"), R("campus"), fallback="campus"),
    "campus_ptm_booked": S(R("ptm", "event_id"), R("campus"), fallback="campus"),
    "campus_ptm_cancelled": S(R("ptm", "event_id"), R("campus"), fallback="campus"),
    "campus_ptm_reminder": S(R("ptm", "event_id"), R("campus"), fallback="campus"),
    "campus_parent_message": S(
        R("parentChat", "thread_id"), R("parentChatList", "campus_id"), R("campus"), fallback="campus"
    ),
    "campus_resource_posted": S(
        R("classResources", "campus_id", "folder_id"),
        R("classResources", "campus_id", "section_id"),
        R("campus"),
        fallback="campus",
    ),
    "campus_recording_ready": S(
        R("campusRecording", "campus_id", "recording_id"),
        R("campusRecording", "campus_id", "live_session_id"),
        R("campusRecording", "campus_id", "section_id"),
        R("campus"),
        fallback="campus",
    ),
    "campus_note_posted": S(R("campusNotes", "campus_id", "section_id"), R("campus"), fallback="campus"),
    "campus_admission": S(
        R("admissions", "campus_id", eq={"kind": "follow_up_digest"}), R("campus"), fallback="campus"
    ),
    "parent_message_received": S(R("classroom", "classroom_id"), R("notifications"), fallback="notifications"),
    "parent_message_replied": S(R("classroom", "classroom_id"), R("notifications"), fallback="notifications"),
}

#: A notif_type that is NOT in SPECS (an old app meeting a newer backend, or a type added without a spec): route by
#: whatever the payload carries; with nothing usable the row itself is shown. Never a dead tap.
DEFAULT_SPEC = S(
    R("chat", "conversation_id"),
    R("post", "post_id"),
    R("story", "story_id"),
    R("classroom", "classroom_id"),
    R("profile", "actor_username"),
    R("notifications"),
    fallback="notifications",
)


class RouteResult(NamedTuple):
    route: str
    target_type: str
    target_id: str
    exact: bool  # False = no specific rule matched, the spec's fallback was used

    def as_dict(self) -> dict:
        return {"route": self.route, "target_type": self.target_type, "target_id": self.target_id}


def _merged(data, classroom_id, session_id) -> dict:
    d = dict(data) if isinstance(data, dict) else {}
    if _present(classroom_id):
        d["classroom_id"] = classroom_id
    if _present(session_id):
        d["session_id"] = session_id
    return d


def route_for(notif_type, data=None, *, classroom_id=None, session_id=None, in_app: bool = False) -> RouteResult:
    """notif_type + payload -> RouteResult. Pure, never raises (odd / missing data -> the spec's fallback)."""
    d = _merged(data, classroom_id, session_id)
    spec = SPECS.get(str(notif_type)) or DEFAULT_SPEC
    route, exact = spec.fallback, False
    for rule in spec.rules:
        if rule.matches(d, in_app=in_app):
            route, exact = rule.route, True
            break
    target_type, args = ROUTES.get(route, ("none", ()))
    target_id = ""
    for key in args:
        if _present(d.get(key)):
            target_id = str(d[key])
            break
    return RouteResult(route, target_type, target_id, exact)


def known_types() -> Iterable[str]:
    return SPECS.keys()


# --------------------------------------------------------------------------------------------------------------------
# Generators (used by tool/gen_notification_routes.py; tested against the committed files so they cannot drift).
# --------------------------------------------------------------------------------------------------------------------
def _dart_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dart_rule(r: Rule) -> str:
    parts = [_dart_str(r.route)]
    if r.requires:
        parts.append("requires: [" + ", ".join(_dart_str(k) for k in r.requires) + "]")
    if r.eq:
        parts.append("eq: {" + ", ".join(f"{_dart_str(k)}: {_dart_str(v)}" for k, v in r.eq) + "}")
    if r.ne:
        parts.append("ne: {" + ", ".join(f"{_dart_str(k)}: {_dart_str(v)}" for k, v in r.ne) + "}")
    if r.truthy:
        parts.append("truthy: [" + ", ".join(_dart_str(k) for k in r.truthy) + "]")
    if r.in_app:
        parts.append("inApp: true")
    return "NotificationRouteRule(" + ", ".join(parts) + ")"


def _dart_spec(spec: RouteSpec) -> str:
    rules = ",\n      ".join(_dart_rule(r) for r in spec.rules)
    return f"NotificationRouteSpec(\n    [\n      {rules},\n    ],\n    {_dart_str(spec.fallback)},\n  )"


def render_dart() -> str:
    out = [
        "// GENERATED by tool/gen_notification_routes.py from backend/backend/core/notification_routes.py - DO NOT EDIT.",
        "// Badalna ho to backend ka SPECS table badlo, phir: python tool/gen_notification_routes.py",
        "// ignore_for_file: lines_longer_than_80_chars",
        "",
        "import 'notification_route_spec.dart';",
        "",
        "/// route name -> payload keys the screen needs (first key = target id). Mirrors notification_routes.ROUTES.",
        "const Map<String, List<String>> kNotificationRouteArgs = {",
    ]
    for route, (_t, args) in ROUTES.items():
        out.append(f"  {_dart_str(route)}: [{', '.join(_dart_str(a) for a in args)}],")
    out += ["};", "", "const Map<String, NotificationRouteSpec> kNotificationRouteSpecs = {"]
    for ntype, spec in SPECS.items():
        out.append(f"  {_dart_str(ntype)}: {_dart_spec(spec)},")
    out += ["};", "", "const NotificationRouteSpec kDefaultNotificationRouteSpec = " + _dart_spec(DEFAULT_SPEC) + ";", ""]
    return "\n".join(out)


def _sample_for(rule: Rule) -> dict:
    d = {k: "1" for k in rule.requires}
    for k, v in rule.eq:
        d[k] = v
    for k in rule.truthy:
        d[k] = "true"
    for k, v in rule.ne:
        d.setdefault(k, "x" if v != "x" else "y")
    return d


def build_cases() -> list[dict]:
    """Shared Python <-> Dart fixture: for every type one case per rule (data that satisfies it), one with an EMPTY
    payload (-> fallback), plus the in-app flavour where a rule is in-app only. `route` is computed here; the Dart test
    must agree with it."""
    cases: list[dict] = []
    for ntype, spec in list(SPECS.items()) + [("__unknown_type__", DEFAULT_SPEC)]:
        seen: list[tuple] = []
        samples = [_sample_for(r) for r in spec.rules] + [{}]
        for sample in samples:
            for in_app in (False, True):
                res = route_for(ntype, sample, in_app=in_app)
                key = (tuple(sorted(sample.items())), in_app, res.route)
                if key in seen:
                    continue
                if in_app and not any(r.in_app for r in spec.rules):
                    continue  # identical to the push case
                seen.append(key)
                cases.append(
                    {"type": ntype, "data": sample, "in_app": in_app, "route": res.route, "target_id": res.target_id}
                )
    return cases
