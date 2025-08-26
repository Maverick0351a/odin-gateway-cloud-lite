from __future__ import annotations

import base64
import datetime
import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """Return canonical JSON bytes (sorted keys, compact separators)."""
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

def sha256_cid(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()

def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)
