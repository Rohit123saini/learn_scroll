"""System checks: loud warnings if launch-critical legal settings are still empty."""
from django.conf import settings
from django.core.checks import Warning, register


@register()
def copyright_legal_settings(app_configs, **kwargs):
    out = []
    if settings.DEBUG:
        return out
    if not (getattr(settings, "GRIEVANCE_OFFICER_NAME", "") and getattr(settings, "GRIEVANCE_OFFICER_EMAIL", "")):
        out.append(Warning(
            "GRIEVANCE_OFFICER_NAME / GRIEVANCE_OFFICER_EMAIL are not set.",
            hint="IT Rules 2021 expect a named Grievance Officer to be published. Set them in the environment.",
            id="copyrights.W001"))
    if not getattr(settings, "LEGAL_ORDERS_EMAIL", ""):
        out.append(Warning(
            "LEGAL_ORDERS_EMAIL is not set.", hint="Tell courts / authorities where to send removal orders.",
            id="copyrights.W002"))
    if getattr(settings, "COPYRIGHT_COUNTER_WAIT_DAYS", 21) < 10:
        out.append(Warning(
            "COPYRIGHT_COUNTER_WAIT_DAYS is below 10.", hint="Confirm the waiting period with your lawyer.",
            id="copyrights.W003"))
    # Task 13 / 14 -- operational safety nets
    if not getattr(settings, "SENTRY_DSN", ""):
        out.append(Warning(
            "SENTRY_DSN is not set: failures of the copyright Celery tasks will NOT alert anyone.",
            hint="Set SENTRY_DSN (and keep CeleryIntegration(monitor_beat_tasks=True)) so missed runs / task "
                 "failures page you. Also schedule `manage.py check_copyright_automation --alert`.",
            id="copyrights.W004"))
    if not getattr(settings, "ADMIN_REQUIRE_2FA", False):
        out.append(Warning(
            "ADMIN_REQUIRE_2FA is off: copyright moderators can log into /admin/ with a password only.",
            hint="Set ADMIN_REQUIRE_2FA=true and enrol each moderator: manage.py setup_staff_2fa <username>.",
            id="copyrights.W005"))
    # DPDP Act 2023 - claimant personal data must not be kept for ever (copyrights/retention.py)
    if (getattr(settings, "COPYRIGHT_PII_RETENTION_DAYS", 365) <= 0
            or getattr(settings, "COPYRIGHT_PII_RETENTION_UPHELD_DAYS", 1095) <= 0):
        out.append(Warning(
            "COPYRIGHT_PII_RETENTION_DAYS / COPYRIGHT_PII_RETENTION_UPHELD_DAYS is 0: claimant personal data is never erased.",
            hint="DPDP Act 2023 s.8(7) expects erasure once the purpose is over. Set a number of days "
                 "(defaults 365 / 1095) and confirm them with your lawyer.",
            id="copyrights.W009"))
    # Automatic re-upload detection (fingerprint.py): each missing tool silently disables one media kind
    if getattr(settings, "COPYRIGHT_FP_ENABLED", True):
        import importlib.util
        import shutil

        for tool, kind, hint, cid in (
            ("ffmpeg", "video", "Install ffmpeg (also ffprobe) on the Celery worker image.", "copyrights.W006"),
            ("fpcalc", "audio", "apt install libchromaprint-tools on the Celery worker image.", "copyrights.W007"),
        ):
            if shutil.which(tool) is None:
                out.append(Warning(f"`{tool}` not found: {kind} re-uploads will NOT be detected by fingerprint.",
                                   hint=hint, id=cid))
        if importlib.util.find_spec("pypdf") is None:
            out.append(Warning("pypdf is not installed: PDF re-uploads will NOT be detected by fingerprint.",
                               hint="pip install -r requirements.txt", id="copyrights.W008"))
    return out
