from __future__ import annotations

import os
from urllib.parse import urlparse


def _parse_allowlist() -> list[str]:
    s = os.getenv("HEL_ALLOWLIST","").strip()
    if not s:
        return []
    # decode any __SL__ -> '/' if caller encoded for env-var compatibility
    s = s.replace("__SL__", "/")
    # strip spaces and empty entries
    items = [h.strip() for h in s.split(",") if h.strip()]
    return items

def check_forward_allowed(forward_url: str | None) -> tuple[bool, str]:
    if not forward_url:
        return True, "no_forward_url"
    host = urlparse(forward_url).hostname or ""
    allow = _parse_allowlist()
    if not allow:
        return False, "deny_no_allowlist_configured"
    return (host in allow, f"{'allowed' if host in allow else 'blocked'}:{host}")
