from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import logging
from threading import Lock
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from . import billing, sft
from .crypto import load_signer_from_env, merge_jwks
from .hel import check_forward_allowed
from .receipts import load_receipt_store
from .utils import canonical_json, now_iso, sha256_cid

_metrics_lock = Lock()
_metrics = {
    "requests_total": 0,
    # simple histogram buckets in seconds
    "latency_buckets": {0.05:0, 0.1:0, 0.25:0, 0.5:0, 1.0:0, 2.5:0, 5.0:0, 10.0:0, float('inf'):0},
    "latency_sum": 0.0,
    "latency_count": 0,
}

app = FastAPI(title="ODIN Gateway Cloud Lite", version="0.1.0")

_req_logger = logging.getLogger("odin.requests")
if not _req_logger.handlers:
    h = logging.StreamHandler()
    if os.getenv("ODIN_REQUEST_LOG_JSON", "false").lower() in {"1", "true", "yes"}:
        class _JsonFmt(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - formatting
                base = {
                    "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                }
                return json.dumps(base, separators=(",", ":"))
        h.setFormatter(_JsonFmt())
    else:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    _req_logger.addHandler(h)
_req_logger.setLevel(getattr(logging, os.getenv("ODIN_REQUEST_LOG_LEVEL", "INFO").upper(), logging.INFO))

@app.middleware("http")
async def _logging_middleware(request: Request, call_next):  # pragma: no cover - thin instrumentation
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration = (time.perf_counter() - start) * 1000.0
        trace_id = request.headers.get("X-Trace-Id") or "-"
        _req_logger.info(f"method={request.method} path={request.url.path} status={response.status_code} dur_ms={duration:.2f} trace_id={trace_id}")
        return response
    except Exception as e:
        duration = (time.perf_counter() - start) * 1000.0
        _req_logger.error(f"method={request.method} path={request.url.path} error={e} dur_ms={duration:.2f}")
        raise

signer = load_signer_from_env()
billing.init_stripe()
store = load_receipt_store()

def _record_metrics(duration: float):
    with _metrics_lock:
        _metrics["requests_total"] += 1
        _metrics["latency_sum"] += duration
        _metrics["latency_count"] += 1
    for b in _metrics["latency_buckets"]:
            if duration <= b:
                _metrics["latency_buckets"][b] += 1
                break

def _prometheus_exposition() -> str:
    lines = [
        "# HELP odin_requests_total Total requests",
        "# TYPE odin_requests_total counter",
        f"odin_requests_total {_metrics['requests_total']}",
        "# HELP odin_request_latency_seconds Request latency",
        "# TYPE odin_request_latency_seconds histogram",
    ]
    # build histogram lines
    cumulative = 0
    for b in sorted(_metrics["latency_buckets"]):
        cumulative += _metrics["latency_buckets"][b]
        bucket_label = (
            "+Inf" if b == float("inf") else f"{b:.2f}".rstrip("0").rstrip(".")
        )
        lines.append(f"odin_request_latency_seconds_bucket{{le=\"{bucket_label}\"}} {cumulative}")
    lines.append(f"odin_request_latency_seconds_sum {_metrics['latency_sum']}")
    lines.append(f"odin_request_latency_seconds_count {_metrics['latency_count']}")
    return "\n".join(lines) + "\n"

class Envelope(BaseModel):
    payload: dict[str, Any]
    payload_type: str
    target_type: str
    trace_id: str | None = None
    ts: str | None = None
    forward_url: str | None = None

@app.get("/healthz")
@app.get("/health")
def healthz():
    return {
        "ok": True,
        "signer": bool(signer),
        "receipts_path": store.path,
        "version": app.version,
    }

@app.get("/.well-known/jwks.json")
def jwks():
    active = signer.public_jwk() if signer else None
    additional = os.getenv("ODIN_ADDITIONAL_PUBLIC_JWKS")
    return merge_jwks(active, additional)

@app.post("/v1/odin/envelope")
def handle_envelope(
    env: Envelope,
    response: Response,
    request: Request,
    x_odin_api_key: str | None = Header(default=None),
    x_odin_api_mac: str | None = Header(default=None),
):
    start = time.perf_counter()
    # Normalize payload via SFT with defensive error handling (prevents stray partial try blocks)
    try:
        normalized = sft.normalize(env.payload, env.payload_type, env.target_type)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Normalization failed: {e}")
    normalized_cid = sha256_cid(canonical_json(normalized))

    # policy check for forward_url (HEL)
    allowed, reason = check_forward_allowed(env.forward_url)

    # compute ts/trace
    ts = env.ts or now_iso()
    trace_id = env.trace_id or f"trace-{ts}"

    # API key MAC verification BEFORE persistence
    secrets_json = os.getenv("ODIN_API_KEY_SECRETS")
    if secrets_json:
        try:
            key_map = json.loads(secrets_json)
        except Exception:
            raise HTTPException(status_code=500, detail="Server key config invalid")
        if not x_odin_api_key or not x_odin_api_mac:
            raise HTTPException(status_code=401, detail="Missing API key or MAC")
        secret = key_map.get(x_odin_api_key)
        if not secret:
            raise HTTPException(status_code=401, detail="Unknown API key")
        mac_message = f"{normalized_cid}|{trace_id}|{ts}".encode()
        expected = base64.urlsafe_b64encode(
            hmac.new(secret.encode("utf-8"), mac_message, hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(expected, x_odin_api_mac):
            raise HTTPException(status_code=401, detail="Invalid MAC")

    # Billing / quota enforcement (project scoped)
    project_id = (
        os.getenv("BILLING_PROJECT_ID")
        or os.getenv("FIRESTORE_PROJECT")
        or "local-project"
    )
    billing.enforce_quota(project_id)

    # Build receipt (only for authorized requests)
    receipt = {
        "trace_id": trace_id,
        # lite: single hop per request; chains build by multiple requests with same trace_id
        "hop": 0,
        "ts": ts,
        "payload_type": env.payload_type,
        "target_type": env.target_type,
        "normalized_cid": normalized_cid,
        "policy": {"engine": "HEL", "allowed": allowed, "reason": reason},
    }
    receipt = store.add(receipt)
    billing.record_receipt(project_id, metered=True)

    # (Optional) forward step (no body on failure in lite mode)
    forwarded = None
    if env.forward_url:
        if not allowed:
            raise HTTPException(status_code=403, detail=f"Forward blocked by HEL: {reason}")
        try:
            with httpx.Client(timeout=10.0) as client:
                fresp = client.post(env.forward_url, json=normalized)
                forwarded = {"status_code": fresp.status_code}
        except Exception as e:
            forwarded = {"error": str(e)}

    # Sign response CID
    response_body = {
        "trace_id": trace_id,
        "receipt": receipt,
        "forwarded": forwarded,
    }
    resp_cid = sha256_cid(canonical_json(response_body))
    response.headers["X-ODIN-Response-CID"] = resp_cid
    if signer:
        msg = f"{resp_cid}|{trace_id}|{ts}".encode()
        sig = signer.sign(msg)
        response.headers["X-ODIN-Signature"] = sig
        response.headers["X-ODIN-KID"] = signer.kid
    duration = time.perf_counter() - start
    _record_metrics(duration)
    return response_body

@app.get("/metrics")
def metrics():
    return Response(content=_prometheus_exposition(), media_type="text/plain; version=0.0.4")

@app.get("/v1/receipts/hops/chain/{trace_id}")
def get_chain(trace_id: str):
    return {"trace_id": trace_id, "chain": store.chain(trace_id)}

@app.get("/v1/receipts/export/{trace_id}")
def export_bundle(trace_id: str, response: Response):
    chain = store.chain(trace_id)
    bundle = {"trace_id": trace_id, "chain": chain, "exported_at": now_iso()}
    bundle_cid = sha256_cid(canonical_json(bundle))
    response.headers["X-ODIN-Response-CID"] = bundle_cid
    if signer:
        msg = f"{bundle_cid}|{trace_id}|{bundle['exported_at']}".encode()
        sig = signer.sign(msg)
        response.headers["X-ODIN-Signature"] = sig
        response.headers["X-ODIN-KID"] = signer.kid
    return bundle


# ---------------- Billing endpoints (experimental) -----------------

def _require_api_key(x_odin_api_key: str | None):
    secrets_json = os.getenv("ODIN_API_KEY_SECRETS")
    if not secrets_json:
        return  # no auth configured
    if not x_odin_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    try:
        key_map = json.loads(secrets_json)
    except Exception:
        raise HTTPException(status_code=500, detail="Server key config invalid")
    if x_odin_api_key not in key_map:
        raise HTTPException(status_code=401, detail="Unknown API key")


@app.get("/v1/billing/usage")
def billing_usage(x_odin_api_key: str | None = Header(default=None)):
    _require_api_key(x_odin_api_key)
    project_id = (
        os.getenv("BILLING_PROJECT_ID")
        or os.getenv("FIRESTORE_PROJECT")
        or "local-project"
    )
    return billing.usage_summary(project_id)


@app.get("/v1/billing/tier")
def billing_tier(x_odin_api_key: str | None = Header(default=None)):
    """Lightweight tier introspection (auth required if API keys configured)."""
    _require_api_key(x_odin_api_key)
    project_id = (
        os.getenv("BILLING_PROJECT_ID")
        or os.getenv("FIRESTORE_PROJECT")
        or "local-project"
    )
    tier = billing.configured_tier(project_id)
    return {"project_id": project_id, "tier": tier, "limit": billing.free_tier_limit() if tier == "free" else None}


class CheckoutRequest(BaseModel):
    success_url: str
    cancel_url: str
    customer_email: str | None = None


@app.post("/v1/billing/checkout")
def create_checkout(req: CheckoutRequest, x_odin_api_key: str | None = Header(default=None)):  # pragma: no cover - network path
    _require_api_key(x_odin_api_key)
    if not billing.stripe_configured():
        raise HTTPException(status_code=400, detail="Stripe not configured")
    project_id = (
        os.getenv("BILLING_PROJECT_ID")
        or os.getenv("FIRESTORE_PROJECT")
        or "local-project"
    )
    try:
        session = billing.create_checkout_session(
            project_id=project_id,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
            customer_email=req.customer_email,
        )
        return session
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/billing/webhook")
async def stripe_webhook(request: Request):  # pragma: no cover - network path
    signature = request.headers.get("stripe-signature")
    body = await request.body()
    if not signature:
        raise HTTPException(status_code=400, detail="Missing Stripe signature header")
    try:
        event = billing.verify_webhook(signature, body)
        handled = billing.handle_webhook_event(event)
        return handled
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
