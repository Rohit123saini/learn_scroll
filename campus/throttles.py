# campus/throttles.py
"""
B-4 fix — `campus` had zero `throttle_scope`/`ScopedRateThrottle` usage
anywhere (confirmed: no hits in views.py before this pass), unlike
`liveclass`/`message`, which throttle every abuse-prone action
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
from rest_framework.throttling import ScopedRateThrottle


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
    'join' endpoint on this viewset (unlike liveclass's
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