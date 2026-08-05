#!/usr/bin/env python3
"""Reuse dtf-helper HTTP client without duplicating code."""

from __future__ import annotations

import sys
from pathlib import Path

_DTF_HELPER_SCRIPTS = Path.home() / ".cursor/skills/dtf-helper/scripts"
if str(_DTF_HELPER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_DTF_HELPER_SCRIPTS))

from dtf_client import env_base, env_cookie, env_host, probe, request, unwrap_data  # noqa: E402

__all__ = [
    "env_base",
    "env_cookie",
    "env_host",
    "probe",
    "request",
    "unwrap_data",
]
