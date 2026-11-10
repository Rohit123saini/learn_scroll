"""P-C3 -- tiny env helpers for settings.py (no Django import, so they are safe to import from anywhere).

A typo like SENTRY_TRACES_SAMPLE_RATE=O.1 must not crash every request (it used to: float() inside the sampler) and
must not silently become something huge either: a bad value logs nothing here (settings load is too early for logging)
but falls back to the default; `core/release_check.py` is what reports it (it reads the same env).
"""
from __future__ import annotations

import os
from typing import Mapping, Optional


def env_float(name: str, default: float, lo: Optional[float] = None, hi: Optional[float] = None,
              env: Optional[Mapping[str, str]] = None) -> float:
    raw = (env if env is not None else os.environ).get(name)
    try:
        val = float(str(raw).strip()) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        val = float(default)
    if val != val:  # NaN
        val = float(default)
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


def env_int(name: str, default: int, lo: Optional[int] = None, hi: Optional[int] = None,
            env: Optional[Mapping[str, str]] = None) -> int:
    raw = (env if env is not None else os.environ).get(name)
    try:
        val = int(str(raw).strip()) if raw not in (None, "") else int(default)
    except (TypeError, ValueError):
        val = int(default)
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


def env_bool(name: str, default: bool = False, env: Optional[Mapping[str, str]] = None) -> bool:
    raw = (env if env is not None else os.environ).get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
