# post/views/_common.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

import logging
from rest_framework.pagination import PageNumberPagination
from django.contrib.auth import get_user_model


User = get_user_model()
logger = logging.getLogger("post.views")  # same logger name as the old post/views.py (log config unchanged)

# ===================== PAGINATION =====================
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class HomeFeedPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50

# ===================== VALIDATION MESSAGE HELPER =====================
def _first_error_text(errors, prefix=""):
    """Flattens DRF's {field: [msg, ...]} error dict into one readable line
    ("category: "x" is not a valid category. ...") for the top-level
    `message`, so a client that only shows `message` (api_service.dart
    does) still tells the user WHAT failed instead of "Validation failed".
    The full dict stays available under `errors`."""
    if isinstance(errors, dict):
        for field, value in errors.items():
            text = _first_error_text(value, field)
            if text:
                return text
    elif isinstance(errors, (list, tuple)):
        for value in errors:
            text = _first_error_text(value, prefix)
            if text:
                return text
    elif errors:
        if prefix and prefix != "non_field_errors":
            return f"{prefix}: {errors}"
        return str(errors)
    return ""
