# campus/throttles.py
"""
B-4 fix — `campus` had zero `throttle_scope`/`ScopedRateThrottle` usage
anywhere (confirmed: no hits in views.py before this pass), unlike
`tuitionclass`/`message`, which throttle every abuse-prone action
(`session_join`, `message_send`, `coupon_validate`, `parent_code_verify_ip`,
etc. — see settings.py's `DEFAULT_THROTTLE_RATES` comments for the full
list and the reasoning behind each one). These four classes cover the
same class of endpoint here: money movement, an unauthenticated-adjacent
token-verification surface, session-start fan-out, and public-facing
posts to a whole roster.

Each subclasses `ScopedRateThrottle` with a hardcoded `scope` instead of
relying on `view.throttle_scope`. That's necessary (not just style)
because several of these actions live as different `@action` methods on
the SAME ViewSet — `FeePaymentViewSet` has `pay`/`record`/`refund`,
`CampusLiveSessionViewSet` has `start`/`end`/`cancel` — and
`view.throttle_scope` is a single class-level attribute shared by every
action on that view. A throttle class with its own fixed `scope`
(resolved via `self.scope`, not `view.throttle_scope`) lets
`@action(throttle_classes=[...])` hand a different limit to each action
on the same ViewSet, the same pattern `message/throttles.py` already
uses for `MessageSendThrottle`/`CallInitiateThrottle`/etc. living
alongside each other.

Matching rate entries are added to `DEFAULT_THROTTLE_RATES` in
settings.py — a `ScopedRateThrottle` (or a subclass of it) with a scope
that has no entry there raises `ImproperlyConfigured` on the very first
request to that action, exactly the bug class already fixed repeatedly
elsewhere in settings.py for `session_join`, `coin_withdrawal`,
`chat_reaction`, etc.
"""
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class _FixedScopeThrottle(SimpleRateThrottle):
    """Per-user (or per-IP when anonymous) throttle with a hard-coded `scope`.
    Unlike `ScopedRateThrottle`, it never reads `view.throttle_scope`, so it
    enforces its own rate even when the view defines no `throttle_scope`."""
    scope = None

    def get_cache_key(self, request, view):
        ident = request.user.pk if request.user and request.user.is_authenticated else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class CampusFeePaymentThrottle(ScopedRateThrottle):
    """FeePaymentViewSet.pay / .record / .refund — every hit moves real
    coin balance one way or another (pay debits via
    CoinLedger.record_transaction; refund credits back; record writes a
    SUCCESS payment straight onto an invoice). Rated like the existing
    coin_withdrawal/coin_purchase scopes for the same reason: this is a
    money-movement endpoint, not a plain read/write action, so a script
    hammering it is a wallet-balance/invoice-desync risk, not just noise."""
    scope = "campus_fee_payment"


class CampusLiveSessionJoinThrottle(ScopedRateThrottle):
    """CampusLiveSessionViewSet.start — campus has no separate student
    'join' endpoint on this viewset (unlike tuitionclass's
    ClassSessionViewSet.join, which the 'campus_live_session_join' scope
    name is modeled on); `start` is the closest analogue here, since
    it's what flips a session LIVE and fires the CAMPUS_SESSION_LIVE
    notification fan-out to every active enrollment in the section (see
    CampusLiveSessionViewSet.start in views.py). Scoped to stop a
    teacher account — compromised, scripted, or just double-tapping —
    from re-triggering that fan-out in a loop."""
    scope = "campus_live_session_join"


class CampusNoticePostThrottle(ScopedRateThrottle):
    """NoticeViewSet.create — any active staff member of a campus can
    post a notice at any scope, campus-wide included (see
    NoticeViewSet's own docstring in views.py: this pass deliberately
    doesn't restrict who can post where). Without a rate limit, one
    staff account — again, compromised or scripted — can spam every
    student/parent in a campus with notices; this is the throttle that
    stands in for that missing scope restriction until role-to-scope
    rules are confirmed."""
    scope = "campus_notice_post"


class CampusEmergencyBroadcastThrottle(ScopedRateThrottle):
    """NoticeViewSet.emergency — an emergency alert buzzes every phone in scope and bypasses quiet
    hours, so even a legitimate admin gets only a handful per hour (stops a double-tap storm or a
    compromised admin account turning it into a spam cannon)."""
    scope = "campus_emergency_broadcast"


class CampusParentLinkVerifyThrottle(ScopedRateThrottle):
    """ParentLinkVerifyView.post — takes a raw `token` from the request
    body and resolves it via bridge.resolve_parent_from_token. Without a
    rate limit this is a token-guessing surface: an authenticated user
    could brute-force someone else's parent-access token and get linked
    to (i.e. gain read access to) an arbitrary student's campus data.
    Scoped tight for the same reason message's parent_code_reveal /
    parent_code_verify_ip scopes are tight — this class of endpoint is
    rated for the abuse case (guessing), not for legitimate retry
    traffic, which is inherently rare (a parent verifies their link
    once, not repeatedly)."""
    scope = "campus_parent_link_verify"


class CampusInviteCodeGenerateThrottle(ScopedRateThrottle):
    """[ADDED — Task 13/G13] CampusInviteCodeGenerateView.post —
    staff-only, so lower abuse risk than the redeem side below, but
    still worth a scope so a scripted/compromised staff token can't
    spin up invite codes in a tight loop."""
    scope = "campus_invite_code_generate"


class CampusInviteCodeRedeemThrottle(ScopedRateThrottle):
    """[ADDED — Task 13/G13] CampusInviteCodeRedeemView.post — takes a
    raw `code` from the request body and looks it up directly (no
    per-user scoping on the lookup itself). Same "code-guessing
    surface" reasoning as `CampusParentLinkVerifyThrottle` above: an
    authenticated user could otherwise brute-force a section's 7-char
    code and self-enroll into a batch they were never actually
    invited to."""
    scope = "campus_invite_code_redeem"


class CampusDoubtPostThrottle(ScopedRateThrottle):
    """[ADDED — T4 §F] CampusDoubtViewSet.create / .reply — a student (or
    teacher) can otherwise spam a class's teachers with doubts/replies,
    each of which fans out a push. Same shape as `CampusNoticePostThrottle`
    above, rated per-user."""
    scope = "campus_doubt_post"


class CampusHomeworkDiaryPostThrottle(ScopedRateThrottle):
    """HomeworkDiaryViewSet.create — every post fans a push out to a whole
    section (students + parents). A class-teacher legitimately posts one line
    per subject per day (~8-10), so the cap is per hour with headroom; it
    only stops a scripted/compromised staff account from buzzing a roster in
    a loop."""
    scope = "campus_homework_diary_post"


class CampusStaffLeaveApplyThrottle(ScopedRateThrottle):
    """StaffLeaveViewSet.create — every application pushes every admin /
    principal of the campus. Generous for real use (a few per month), tight
    enough to stop a scripted account from buzzing the office."""
    scope = "campus_staff_leave_apply"


class CampusSubstituteAssignThrottle(ScopedRateThrottle):
    """SubstituteAssignmentViewSet.create / TeacherAbsenceViewSet.create —
    every assignment pushes the substitute AND a whole section (students +
    parents). A busy morning is one absent teacher x ~8 periods, so the cap is
    per hour with headroom; it only stops a scripted/compromised admin account
    from buzzing rosters in a loop."""
    scope = "campus_substitute_assign"


class CampusResultsPublishThrottle(_FixedScopeThrottle):
    """ResultEntryViewSet.publish / .bulk(publish=true) / .import(commit) —
    every publish fans a push out to students + parents of a whole section,
    so cap it the same way as the homework diary. A teacher publishes a
    handful of subject sheets per exam; the cap only stops a scripted or
    compromised staff account from buzzing a roster in a loop."""
    scope = "campus_results_publish"


class CampusResultsImportThrottle(_FixedScopeThrottle):
    """ResultEntryViewSet.import (preview + commit) — parses an uploaded
    CSV, so each hit costs CPU and DB lookups; preview is retried while the
    teacher fixes their file, hence a per-hour cap with headroom."""
    scope = "campus_results_import"


class CampusPTMBookThrottle(_FixedScopeThrottle):
    """PTMBookingViewSet.create / .reschedule — a parent books a handful of
    slots per event; the cap only stops a script from grabbing every slot of a
    school (booking is first-come-first-served)."""
    scope = "campus_ptm_book"


class CampusPTMManageThrottle(_FixedScopeThrottle):
    """PTMEventViewSet.create / .publish / .cancel and slot opening — publishing
    or cancelling fans a push out to every invited parent of the campus."""
    scope = "campus_ptm_manage"


class CampusParentChatSendThrottle(_FixedScopeThrottle):
    """ParentChatViewSet.send — every message pushes the other side. The
    unanswered-in-a-row rule limits one conversation; this limits one account
    across all of its conversations (a teacher with 40 parents, a parent with
    3 teachers)."""
    scope = "campus_parent_chat_send"


class CampusResourceUploadThrottle(_FixedScopeThrottle):
    """ClassResourceViewSet.create / edits that replace the file — uploads are
    the heaviest writes in the campus app (a video can be 200 MB). A teacher
    sharing a chapter posts a handful of files at a time, so 60/hour leaves
    headroom; it stops a scripted or compromised staff account from filling
    storage in a loop."""
    scope = "campus_resource_upload"


class CampusResourceFolderThrottle(_FixedScopeThrottle):
    """ResourceFolderViewSet.create — folders are cheap, but an unbounded
    create loop would still bloat every student's folder list."""
    scope = "campus_resource_folder"


class CampusRecordingControlThrottle(_FixedScopeThrottle):
    """CampusLiveSessionViewSet.start_recording / .stop_recording and the recording
    hide / show action — each start calls the video service (an egress job that costs
    money); a stuck app tapping Start in a loop must not spawn jobs."""
    scope = "campus_recording_control"


class CampusNoteWriteThrottle(_FixedScopeThrottle):
    """ClassNoteViewSet / StudentNoteViewSet writes — a teacher posts a few notes a
    day and a student jots a lot more; this only stops a scripted loop."""
    scope = "campus_note_write"


class CampusFinanceExportThrottle(_FixedScopeThrottle):
    """FINANCE — report / CSV / XLSX exports. Each one scans many rows, so cap per user."""
    scope = "campus_finance_export"


class CampusFinanceReconcileThrottle(_FixedScopeThrottle):
    """FINANCE — reconciliation runs (CSV import / Razorpay API pull / internal checks)."""
    scope = "campus_finance_reconcile"


class CampusReportExportThrottle(_FixedScopeThrottle):
    """REPORTS / EXPORT — attendance / marks / fees exports (CSV, Excel, PDF). Each one scans many rows,
    so cap per user."""
    scope = "campus_report_export"


class CampusSafetyWriteThrottle(_FixedScopeThrottle):
    """Safety — create a backup / record consent for someone / decide a deletion request. Rare admin actions;
    the limit only exists to stop a script from filling the disk with backups."""
    scope = "campus_safety_write"


class CampusSettingsWriteThrottle(_FixedScopeThrottle):
    """Campus Settings — profile / grading / working days / bulk holidays / slot generation. Rare admin saves;
    the limit only stops a script from hammering the bulk endpoints."""
    scope = "campus_settings_write"


class CampusSafetyRestoreThrottle(_FixedScopeThrottle):
    """Safety — restore (dry-run included: it reads and checks the whole file)."""
    scope = "campus_safety_restore"


class CampusDataRequestThrottle(_FixedScopeThrottle):
    """Safety — a user filing / cancelling a data-deletion request or changing a consent."""
    scope = "campus_data_request"


class CampusAdmissionWriteThrottle(ScopedRateThrottle):
    """AdmissionViewSet.create / edit / move / enroll / import — office staff typing in enquiries all day;
    generous for real use, tight enough to stop a scripted account from filling the table."""
    scope = "campus_admission_write"
