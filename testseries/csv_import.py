# testseries/csv_import.py
"""Compatibility shim — the implementation moved to `common/question_csv.py` so
`assigments` can use the same importer without importing `testseries`."""
from common.question_csv import (  # noqa: F401
    MAX_OPTIONS, MAX_ROWS, CsvImportResult, parse_csv,
)
