# login/exceptions.py
"""
Moved. This module used to be a second, drifting copy of the project's error
handler (it knew TokenExpiredError, the live one did not -- so the live one
never sent TOKEN_EXPIRED). Nothing imported it. There is now exactly ONE
handler, ``tuitionclass.exceptions.tuitionclass_exception_handler`` (wired in
settings.REST_FRAMEWORK), and the login app's envelope helpers live in
``login/errors.py``.
"""
from tuitionclass.exceptions import tuitionclass_exception_handler  # noqa: F401
from .errors import api_error, error_body, retry_error  # noqa: F401
