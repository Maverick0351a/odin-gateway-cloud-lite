#!/usr/bin/env python
"""Compute ODIN API MAC for a normalized payload.

Usage:
  python scripts/compute_mac.py --api-key KEY --secret SECRET --payload-file payload.json \
      --payload-type TYPE --target-type TARGET [--trace TRACE] [--ts ISO8601]

Steps performed:
 1. Load JSON payload file.
 2. Normalize using sft.normalize (same as server) to get canonical form.
 3. Compute canonical JSON bytes and CID (sha256:hex).
 4. Build message: <cid>|<trace>|<ts>
 5. Output base64url MAC (HMAC-SHA256) with trailing '=' stripped.

Prints JSON with fields: cid, trace, ts, mac.
"""
import argparse, json, sys, time, hmac, hashlib, base64, datetime
from pathlib import Path

# Local imports assuming run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import sft  # noqa: E402
from app.utils import canonical_json, sha256_cid, now_iso  # noqa: E402

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--api-key', required=True)
    p.add_argument('--secret', required=True, help='Shared secret corresponding to API key')
    p.add_argument('--payload-file', required=True)
    p.add_argument('--payload-type', required=True)
    p.add_argument('--target-type', required=True)
    p.add_argument('--trace', help='Trace ID (defaults to generated)')
    p.add_argument('--ts', help='Timestamp ISO8601 (defaults to now UTC)')
    args = p.parse_args()

    raw = json.loads(Path(args.payload_file).read_text(encoding='utf-8'))
    normalized = sft.normalize(raw, args.payload_type, args.target_type)
    cid = sha256_cid(canonical_json(normalized))
    ts = args.ts or now_iso()
    trace = args.trace or f'trace-{ts}'
    mac_msg = f"{cid}|{trace}|{ts}".encode()
    mac = b64u(hmac.new(args.secret.encode('utf-8'), mac_msg, hashlib.sha256).digest())
    out = {"api_key": args.api_key, "cid": cid, "trace": trace, "ts": ts, "mac": mac}
    print(json.dumps(out, indent=2))

if __name__ == '__main__':
    main()
