"""
user_profile/contact_discovery.py  (S4b -- "find me by phone / e-mail")

POST /profile/contacts/match/   body: {"contacts": [{"ref": "c1", "phones": ["+919876543210"], "emails": ["a@b.com"]}, ...]}

The phone's address book is read ON THE DEVICE; the app sends only the numbers / addresses it wants to look up. This
endpoint answers "which of these belong to a LearnScroll account that may be found this way?" and returns, per contact
`ref`, the matching accounts (id, username, name, photo). Nothing that was sent is stored or logged.

Who can be found (every rule is a "no" that only ever removes a person):
  * `PrivacySettings.find_by_phone` / `find_by_email` (the "Find me by phone / e-mail" switches in Privacy controls)
    must be on. Minors are never found (their switches are locked off, `privacy.discoverable_owner_ids`).
  * the number must be a VERIFIED one (`User.phone_verified`); an e-mail must be the address the person verified
    (`NotificationPreference.email_verified_address`) -- otherwise typing somebody else's number would "find" them.
  * "Hide me from search" is respected the same way as in people search (an accepted follow link still shows them).
  * blocked people (either direction), the caller, inactive accounts: never returned.

Abuse limits: at most MAX_CONTACTS contacts, MAX_IDS_PER_CONTACT phones + e-mails each, MAX_IDENTIFIERS in total and
MAX_MATCHES results per call; the endpoint has its own hourly throttle (`contact_match`). A request over a limit is
refused whole (400) rather than half-answered, so a script cannot probe "just a little more".
"""
import re

from django.conf import settings
from django.db.models.functions import Lower
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

MAX_CONTACTS = 500
MAX_IDS_PER_CONTACT = 5
MAX_IDENTIFIERS = 2000
MAX_MATCHES = 300

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def default_country_code() -> str:
    code = re.sub(r"\D", "", str(getattr(settings, "CONTACT_DISCOVERY_DEFAULT_COUNTRY_CODE", "91") or ""))
    return code or "91"


def normalize_phone(raw):
    """`+<digits>` (E.164 shape) or None. A bare 10-digit number gets the default country code; `0` / `00` prefixes are
    dropped; spaces, dashes and brackets are ignored. Same length rule as `login.models.phone_validator`."""
    text = str(raw or "").strip()
    if not text:
        return None
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if not plus:
        if digits.startswith("00"):
            digits = digits[2:]
        elif digits.startswith("0"):
            digits = default_country_code() + digits[1:]
        elif len(digits) == 10:
            digits = default_country_code() + digits
    if not re.fullmatch(r"[1-9]\d{7,14}", digits):
        return None
    return "+" + digits


def normalize_email(raw):
    text = str(raw or "").strip().lower()
    if not text or len(text) > 254 or not _EMAIL_RE.match(text):
        return None
    return text


class ContactMatchError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_contacts(payload):
    """Validate the request body and return `[(ref, {phones}, {emails}), ...]` (identifiers normalised). Raises
    ContactMatchError for a body over a limit / of the wrong shape. Unreadable single numbers / addresses are skipped."""
    contacts = payload.get("contacts") if isinstance(payload, dict) else None
    if not isinstance(contacts, list) or not contacts:
        raise ContactMatchError("contacts_required", "Send a non-empty `contacts` list.")
    if len(contacts) > MAX_CONTACTS:
        raise ContactMatchError("too_many_contacts", f"At most {MAX_CONTACTS} contacts per request.")
    out, total = [], 0
    for index, item in enumerate(contacts):
        if not isinstance(item, dict):
            raise ContactMatchError("bad_contact", "Each contact must be an object.")
        ref = str(item.get("ref") if item.get("ref") is not None else index)[:64]
        raw_phones = item.get("phones") or []
        raw_emails = item.get("emails") or []
        if not isinstance(raw_phones, list) or not isinstance(raw_emails, list):
            raise ContactMatchError("bad_contact", "`phones` and `emails` must be lists.")
        if len(raw_phones) + len(raw_emails) > MAX_IDS_PER_CONTACT * 2:
            raise ContactMatchError("too_many_identifiers", f"At most {MAX_IDS_PER_CONTACT} phones and {MAX_IDS_PER_CONTACT} e-mails per contact.")
        total += len(raw_phones) + len(raw_emails)
        if total > MAX_IDENTIFIERS:
            raise ContactMatchError("too_many_identifiers", f"At most {MAX_IDENTIFIERS} numbers / e-mails per request.")
        phones = {p for p in (normalize_phone(x) for x in raw_phones) if p}
        emails = {e for e in (normalize_email(x) for x in raw_emails) if e}
        out.append((ref, phones, emails))
    return out


def find_matches(viewer, contacts):
    """`contacts` from parse_contacts -> `[(ref, user), ...]` (a user can appear under several refs, once per ref)."""
    from django.contrib.auth import get_user_model

    from . import privacy
    from .services import blocked_user_ids

    User = get_user_model()
    all_phones = {p for _ref, phones, _e in contacts for p in phones}
    all_emails = {e for _ref, _p, emails in contacts for e in emails}
    if not all_phones and not all_emails:
        return []

    base = User.objects.filter(is_active=True).exclude(pk=viewer.pk).exclude(pk__in=blocked_user_ids(viewer))
    base = privacy.exclude_hidden_from_search(base, viewer)
    # P-F16 -- belt and braces: a minor already has find-by-phone / e-mail forced off (privacy locks); this also covers the
    # case where those locks are switched off by PRIVACY_MINOR_LOCKS.
    from common import minor_policy

    base = minor_policy.exclude_minors_for_stranger(base, viewer, feature=minor_policy.SEARCH)

    by_phone, by_email = {}, {}
    if all_phones:
        # a number may be stored with or without the leading "+"
        wanted = set(all_phones) | {p[1:] for p in all_phones}
        rows = list(base.filter(phone__in=wanted, phone_verified=True))
        ok = privacy.discoverable_owner_ids([u.pk for u in rows], "phone")
        for user in rows:
            if user.pk in ok:
                by_phone["+" + str(user.phone).lstrip("+")] = user
    if all_emails:
        rows = list(
            base.annotate(_email_lower=Lower("email"))
            .filter(_email_lower__in=all_emails)
            .select_related("notification_preference")
        )
        ok = privacy.discoverable_owner_ids([u.pk for u in rows], "email")
        for user in rows:
            if user.pk not in ok:
                continue
            pref = getattr(user, "notification_preference", None)
            verified = str(getattr(pref, "email_verified_address", "") or "").strip().lower()
            if verified and verified == str(user.email or "").strip().lower():
                by_email[verified] = user

    matches = []
    for ref, phones, emails in contacts:
        seen = set()
        for phone in phones:
            user = by_phone.get(phone)
            if user is not None and user.pk not in seen:
                seen.add(user.pk)
                matches.append((ref, user))
        for email in emails:
            user = by_email.get(email)
            if user is not None and user.pk not in seen:
                seen.add(user.pk)
                matches.append((ref, user))
        if len(matches) >= MAX_MATCHES:
            break
    return matches[:MAX_MATCHES]


class ContactMatchView(APIView):
    """S4b -- POST /profile/contacts/match/ (see the module docstring for the rules)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "contact_match"

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT},
                   description="Which of these phone numbers / e-mails belong to people who may be found that way?")
    def post(self, request):
        from .serializers import UserSearchSerializer

        try:
            contacts = parse_contacts(request.data)
        except ContactMatchError as exc:
            return Response({"status": False, "code": exc.code, "message": exc.message},
                            status=status.HTTP_400_BAD_REQUEST)
        matches = find_matches(request.user, contacts)
        data = [
            {"ref": ref, "user": UserSearchSerializer(user, context={"request": request}).data}
            for ref, user in matches
        ]
        return Response({"status": True, "message": "Contacts matched.", "data": data})
