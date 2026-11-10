"""
copyrights/policy.py - the public Copyright Policy (JSON for the app, HTML for the web / store listing).

All contact details and every number comes from settings, so the page can never
disagree with what the code really does (counter-notice wait, strike rules, deadlines).
Fill the GRIEVANCE_* / DMCA_* / LEGAL_ORDERS_EMAIL settings before launch - a Django
system check warns while they are empty.
"""

from html import escape

from django.conf import settings
from django.utils import timezone

from .retention import retention_days


def _s(name, default=""):
    return getattr(settings, name, default) or default


def policy_context():
    officer = {
        "name": _s("GRIEVANCE_OFFICER_NAME"), "designation": _s("GRIEVANCE_OFFICER_DESIGNATION", "Grievance Officer"),
        "email": _s("GRIEVANCE_OFFICER_EMAIL"), "phone": _s("GRIEVANCE_OFFICER_PHONE"),
        "address": _s("GRIEVANCE_OFFICER_ADDRESS"),
    }
    dmca = {
        "name": _s("DMCA_AGENT_NAME"), "email": _s("DMCA_AGENT_EMAIL"),
        "phone": _s("DMCA_AGENT_PHONE"), "address": _s("DMCA_AGENT_ADDRESS"),
    }
    return {
        "app_name": _s("APP_NAME", "LearnScroll"),
        "company_name": _s("COMPANY_LEGAL_NAME") or _s("APP_NAME", "LearnScroll"),
        "copyright_email": _s("COPYRIGHT_EMAIL"),
        "legal_orders_email": _s("LEGAL_ORDERS_EMAIL"),
        "grievance_officer": officer,
        "dmca_agent": dmca if dmca["email"] else None,
        "process": {
            "counter_notice_wait_days": getattr(settings, "COPYRIGHT_COUNTER_WAIT_DAYS", 21),
            "strike_valid_days": getattr(settings, "COPYRIGHT_STRIKE_DAYS", 180),
            "uploads_blocked_at_strikes": getattr(settings, "COPYRIGHT_RESTRICT_AT", 2),
            "termination_review_at_strikes": getattr(settings, "COPYRIGHT_REVIEW_AT", 3),
            "grievance_ack_hours": getattr(settings, "GRIEVANCE_ACK_HOURS", 24),
            "grievance_resolve_days": getattr(settings, "GRIEVANCE_RESOLVE_DAYS", 15),
            "pii_retention_days": getattr(settings, "COPYRIGHT_PII_RETENTION_DAYS", 365),
            "pii_retention_upheld_days": getattr(settings, "COPYRIGHT_PII_RETENTION_UPHELD_DAYS", 1095),
            "email_code_retention_days": retention_days()["email_code"],
        },
        "configured": bool(officer["name"] and officer["email"]),
    }


def _contact_block(c):
    rows = [(k.title(), c.get(k)) for k in ("name", "designation", "email", "phone", "address") if c.get(k)]
    if not rows:
        return "<p><em>Details will be published here.</em></p>"
    return "<ul>" + "".join(f"<li><strong>{escape(k)}:</strong> {escape(str(v))}</li>" for k, v in rows) + "</ul>"


def render_policy_html():
    c = policy_context()
    p = c["process"]
    app = escape(c["app_name"])
    mail = escape(c["copyright_email"]) if c["copyright_email"] else "the in-app copyright form"
    dmca = ""
    if c["dmca_agent"]:
        dmca = f"<h2>Designated agent (US DMCA)</h2>{_contact_block(c['dmca_agent'])}"
    orders = ""
    if c["legal_orders_email"]:
        orders = (f"<p>Court orders and government or authority notices should be sent to "
                  f"<strong>{escape(c['legal_orders_email'])}</strong>.</p>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Copyright Policy - {app}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#222}}
h1,h2{{line-height:1.25}}li{{margin:.25rem 0}}.muted{{color:#666;font-size:.9rem}}</style></head><body>
<h1>Copyright Policy</h1>
<p class="muted">{escape(c['company_name'])} &middot; last generated {timezone.now():%d %B %Y}</p>
<p>{app} respects the rights of creators. Users may only post content they own or have permission to use.</p>

<h2>Report copyright infringement</h2>
<p>If you own a work (or act for the owner) and it is used without permission, use the <strong>Report &rarr; Copyright</strong>
option on the content, then complete the copyright notice in the app{(' (or write to ' + mail + ')') if c['copyright_email'] else ''}.
Before filing, verify the e-mail address you give on the notice (we send a code). A valid notice names the work,
identifies the content, includes a good-faith statement, a statement that the information is accurate, and your signature.
Attaching proof of ownership (for example the original file, a licence or a registration) lets us act faster. Notices that are knowingly false can lead to the loss of your right to file notices and may have legal consequences.</p>
<p>When a complete notice from a verified rights holder with proof arrives, the content is hidden while we review it.
Notices without proof, or from brand-new accounts, are reviewed first and the content stays visible meanwhile.
If the claim is accepted the content is removed and the files are taken offline.</p>

<h2>Counter-notice</h2>
<p>If your content was removed or hidden by mistake you can file a counter-notice in the app (Settings &rarr; Copyright).
We tell the person who complained. If they do not tell us they have started court action within
<strong>{p['counter_notice_wait_days']} days</strong>, the content is restored.</p>

<h2>Repeat infringers</h2>
<p>An accepted copyright claim gives the account a strike. A strike stays for <strong>{p['strike_valid_days']} days</strong>.
At <strong>{p['uploads_blocked_at_strikes']} active strikes</strong> uploading is blocked; at
<strong>{p['termination_review_at_strikes']} active strikes</strong> the account is reviewed for termination.
Strikes are removed if the claim is withdrawn, reversed, or a counter-notice succeeds.</p>

<h2>Court orders and government requests</h2>
<p>We remove content when a court or a competent authority orders us to, within the time the law gives us.
Such removals are not copyright strikes.</p>{orders}

<h2>How long we keep your details (DPDP Act 2023)</h2>
<p>When you file a copyright notice we keep your name, e-mail address, organisation, signature and the proof you
upload only for as long as the notice needs them. When a notice is closed (rejected, withdrawn or content restored) we
erase them <strong>{p['pii_retention_days']} days</strong> after the last change. If the notice was accepted, or found to be
in bad faith, we keep them for <strong>{p['pii_retention_upheld_days']} days</strong> (appeals, strike history and legal claims),
then erase them. The proof files are deleted together with them. E-mail verification codes are deleted after
<strong>{p['email_code_retention_days']} days</strong>. Details are kept longer only when a court or authority requires it
or while a dispute or appeal about the notice is still open. What stays afterwards contains no personal details of the
rights holder: the decision, its date and the strike record. To ask for earlier erasure, write to the Grievance Officer
below.</p>

<h2>Transparency report</h2>
<p>We publish how many copyright claims we receive, how much content we remove and how much we restore:
<a href="/legal/transparency-report/">Transparency Report</a>.</p>

<h2>Grievance Officer</h2>
{_contact_block(c['grievance_officer'])}
<p>We acknowledge complaints within <strong>{p['grievance_ack_hours']} hours</strong> and aim to resolve them within
<strong>{p['grievance_resolve_days']} days</strong>.</p>
{dmca}
</body></html>"""
