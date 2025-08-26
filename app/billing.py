import datetime
import json
import os
import logging
from collections import deque
from typing import Any, Dict, Callable

import stripe  # type: ignore

# Billing & usage scaffold with gradual enhancement toward full Stripe integration.
# Tiers:
#   free        - hard monthly limit (env overrideable)
#   pro         - no hard limit; metered usage (placeholder for Stripe usage records)
#   enterprise  - unlimited here (enforce via contract off-platform)

FREE_LIMIT_DEFAULT = 500
USAGE_FLUSH_INTERVAL = 50  # flush to Firestore every N increments (if configured)

_usage_cache: dict[str, int] = {}
_usage_cache_month: str | None = None
# subscription state cache: project_id -> {tier, status, usage_item?}
_subscription_cache: dict[str, Dict[str, str]] = {}
_processed_event_ids: deque[str] = deque(maxlen=500)  # in-memory webhook idempotency window

# Hook for publishing metered usage (injected for tests)
_usage_publisher: Callable[[str, int, int], None] | None = None  # args: subscription_item, quantity, timestamp


def utc_now() -> datetime.datetime:
    """Timezone-aware UTC now (replacement for deprecated utcnow)."""
    return datetime.datetime.now(datetime.UTC)


def _logger() -> logging.Logger:
    # Defer import to avoid circular in edge cases
    from .logging_config import get_logger  # type: ignore

    return get_logger("odin.billing", level_env="ODIN_BILLING_LOG_LEVEL", default_level="WARNING")


def _current_month_key() -> str:
    now = utc_now()
    return f"{now.year:04d}-{now.month:02d}"


def _reset_month_if_needed():
    global _usage_cache_month, _usage_cache
    mk = _current_month_key()
    if _usage_cache_month != mk:
        _usage_cache = {}
        _usage_cache_month = mk


def configured_tier(project_id: str | None = None) -> str:
    """Return active tier.

    Priority:
      1. Explicit ODIN_BILLING_TIER env if value in (free, pro, enterprise)
         or if set to another known static tier (starter, team).
      2. If env is 'auto' (or unset), attempt dynamic subscription lookup (Firestore or cache).
      3. Fallback to 'free'.
    """
    val = os.getenv("ODIN_BILLING_TIER", "auto").lower()
    if val != "auto" and val:
        return val
    if project_id:
        sub = _load_subscription_state(project_id)
        if sub:
            return sub.get("tier", "free")
    return "free"


def free_tier_limit() -> int:
    try:
        return int(os.getenv("FREE_TIER_MONTHLY_RECEIPT_LIMIT", str(FREE_LIMIT_DEFAULT)))
    except Exception:
        return FREE_LIMIT_DEFAULT


def stripe_configured() -> bool:
    return bool(os.getenv("STRIPE_API_KEY"))


def init_stripe():  # pragma: no cover - simple env wiring
    key = os.getenv("STRIPE_API_KEY")
    if key:
        stripe.api_key = key


def current_usage(project_id: str) -> int:
    _reset_month_if_needed()
    return _usage_cache.get(project_id, 0)


def record_receipt(project_id: str, metered: bool = True) -> None:
    """Increment in-memory usage and periodically persist.

    Firestore persistence (if FIRESTORE_PROJECT set) stores aggregated monthly count:
      collection: billing_usage
        doc id: {project_id}_{YYYY-MM}
        fields: project_id, month, count, updated_at
    """
    _reset_month_if_needed()
    _usage_cache[project_id] = _usage_cache.get(project_id, 0) + 1
    count = _usage_cache[project_id]
    # Periodic flush to Firestore
    flush_every = _env_int("USAGE_FLUSH_INTERVAL", USAGE_FLUSH_INTERVAL)
    if flush_every > 0 and count % flush_every == 0:
        _persist_usage_firestore(project_id, count)
    # Metered usage: publish increment if enabled via env and tier supports it
    if metered and configured_tier(project_id) in {"pro", "team", "enterprise"}:
        _maybe_publish_metered_usage(project_id, 1)


def enforce_quota(project_id: str):
    tier = configured_tier(project_id)
    if tier == "free" and current_usage(project_id) >= free_tier_limit():
        from fastapi import HTTPException  # local import to avoid dependency cycles

        raise HTTPException(
            status_code=402,
            detail="Free tier monthly receipt quota exceeded",
        )


def usage_summary(project_id: str) -> Dict[str, Any]:
    """Return usage, limit, tier, month (combining in-memory + Firestore, favoring memory)."""
    _reset_month_if_needed()
    month = _usage_cache_month or _current_month_key()
    current = current_usage(project_id)
    # If memory count is zero, attempt to load persisted (warm start scenario)
    if current == 0:
        persisted = _load_usage_firestore(project_id, month)
        if persisted is not None:
            _usage_cache[project_id] = persisted
            current = persisted
    active_tier = configured_tier(project_id)
    limit_val = free_tier_limit() if active_tier == "free" else None
    return {
        "project_id": project_id,
        "tier": active_tier,
        "month": month,
        "usage": current,
        "limit": limit_val,
    }


# --- Firestore persistence helpers -------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _firestore_client():  # pragma: no cover - lazy import pattern
    project = os.getenv("FIRESTORE_PROJECT")
    if not project:
        return None
    try:
        from google.cloud import firestore  # type: ignore

        return firestore.Client(project=project)  # type: ignore
    except Exception:
        return None


def _usage_doc_id(project_id: str, month: str) -> str:
    return f"{project_id}_{month}"


def _persist_usage_firestore(project_id: str, count: int):  # pragma: no cover - network
    client = _firestore_client()
    if not client:
        return
    month = _current_month_key()
    doc_id = _usage_doc_id(project_id, month)
    data = {
        "project_id": project_id,
        "month": month,
        "count": count,
        "updated_at": utc_now().isoformat().replace("+00:00", "Z"),
    }
    try:
        client.collection("billing_usage").document(doc_id).set(data)
    except Exception as e:
        _logger().debug(f"Failed persisting usage to Firestore (non-fatal): {e}")


def _load_usage_firestore(project_id: str, month: str) -> int | None:  # pragma: no cover
    client = _firestore_client()
    if not client:
        return None
    doc_id = _usage_doc_id(project_id, month)
    try:
        snap = client.collection("billing_usage").document(doc_id).get()
        if snap.exists:  # type: ignore[attr-defined]
            data = snap.to_dict()  # type: ignore
            return int(data.get("count", 0))
    except Exception as e:
        _logger().debug(f"Failed loading usage from Firestore: {e}")
        return None
    return None


# --- Stripe checkout & webhook scaffolding ----------------------------------------

def required_prices_present() -> bool:
    # Require base subscription price; usage (metered) price optional
    return bool(os.getenv("STRIPE_PRICE_PRO"))


def create_checkout_session(
    project_id: str,
    success_url: str,
    cancel_url: str,
    customer_email: str | None = None,
) -> dict[str, Any]:  # pragma: no cover - network
    if not stripe_configured():
        raise RuntimeError("Stripe not configured")
    if not required_prices_present():
        raise RuntimeError("Stripe price env vars missing")
    price = os.getenv("STRIPE_PRICE_PRO")
    usage_price = os.getenv("STRIPE_PRICE_USAGE")  # optional metered add-on
    line_items: list[dict[str, Any]] = [{"price": price, "quantity": 1}]
    if usage_price:
        # For metered prices Stripe expects no quantity
        line_items.append({"price": usage_price})
    params: Dict[str, Any] = {
        "mode": "subscription",
        "line_items": line_items,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {"project_id": project_id},
    }
    if customer_email:
        params["customer_email"] = customer_email
    session = stripe.checkout.Session.create(**params)  # type: ignore
    return {"id": session.id, "url": session.url}  # type: ignore[attr-defined]


def verify_webhook(signature_header: str, payload: bytes):  # pragma: no cover - network
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("Missing STRIPE_WEBHOOK_SECRET")
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature_header,
            secret=secret,
        )
        return event
    except Exception as e:
        _logger().warning(f"Webhook verification failed: {e}")
        raise


def handle_webhook_event(event: Any) -> dict[str, Any]:  # pragma: no cover - placeholder
    """Process Stripe events with tier inference, usage item extraction & idempotency."""
    etype = getattr(event, "type", None)
    event_id = getattr(event, "id", None)
    data_obj = getattr(event, "data", {}).get("object") if hasattr(event, "data") else None
    project_id = None
    changed = False
    # Fast in-memory idempotency check
    if event_id and event_id in _processed_event_ids:
        return {"received": True, "idempotent": True, "type": etype, "project_id": None, "tier_changed": False, "tier": None}
    # Optional persistent idempotency (Firestore) for multi-replica reliability
    if event_id and os.getenv("BILLING_PERSIST_IDEMPOTENCY", "").lower() in {"1", "true", "yes", "on"}:
        if _persistent_event_seen(event_id):  # pragma: no cover - network
            _processed_event_ids.append(event_id)
            return {"received": True, "idempotent": True, "type": etype, "project_id": None, "tier_changed": False, "tier": None}
    try:
        if data_obj and isinstance(data_obj, dict):
            meta = data_obj.get("metadata") or {}
            project_id = meta.get("project_id")
        if etype == "checkout.session.completed" and project_id:
            try:
                sid = data_obj.get("id")
                session = stripe.checkout.Session.retrieve(sid, expand=["line_items"])  # type: ignore
                price_ids = [li.price.id for li in session.line_items.data]  # type: ignore[attr-defined]
            except Exception as e:
                _logger().debug(f"Checkout session retrieve failed: {e}")
                price_ids = []
            tier = _infer_tier_from_prices(price_ids)
            if tier:
                usage_item = None
                try:
                    line_items = session.line_items.data  # type: ignore[attr-defined]
                    usage_price = os.getenv("STRIPE_PRICE_USAGE")
                    if usage_price:
                        for li in line_items:
                            if getattr(li, "price", None) and getattr(li.price, "id", None) == usage_price:  # type: ignore[attr-defined]
                                usage_item = getattr(li, "id", None)
                                break
                except Exception as e:
                    _logger().debug(f"Line item parse failed: {e}")
                _record_subscription_state(project_id, tier, "active", usage_item=usage_item)
                changed = True
        elif etype in {"customer.subscription.updated", "customer.subscription.created"}:
            items = []
            price_ids = []
            if data_obj and isinstance(data_obj, dict):
                items = data_obj.get("items", {}).get("data", [])
                price_ids = [it.get("price", {}).get("id") for it in items]
                if not project_id:
                    for it in items:
                        pid = it.get("price", {}).get("metadata", {}).get("project_id")
                        if pid:
                            project_id = pid
                            break
            tier = _infer_tier_from_prices(price_ids)
            if tier and project_id:
                status = data_obj.get("status", "active") if isinstance(data_obj, dict) else "active"
                usage_item = None
                usage_price = os.getenv("STRIPE_PRICE_USAGE")
                if usage_price:
                    for it in items:
                        price_id = it.get("price", {}).get("id")
                        if price_id == usage_price:
                            usage_item = it.get("id")
                            break
                _record_subscription_state(project_id, tier, status, usage_item=usage_item)
                changed = True
    except Exception as e:
        _logger().warning(f"Webhook handling error: {e}")
    if event_id:
        _processed_event_ids.append(event_id)
        if os.getenv("BILLING_PERSIST_IDEMPOTENCY", "").lower() in {"1", "true", "yes", "on"}:
            _persist_processed_event_id(event_id)  # pragma: no cover - network
    return {"received": True, "idempotent": False, "type": etype, "project_id": project_id, "tier_changed": changed, "tier": configured_tier(project_id) if project_id else None}


def _infer_tier_from_prices(price_ids: list[str]) -> str | None:
    if not price_ids:
        return None
    mapping = {
        os.getenv("STRIPE_PRICE_TEAM"): "team",
        os.getenv("STRIPE_PRICE_PRO"): "pro",
        os.getenv("STRIPE_PRICE_STARTER"): "starter",
    }
    for pid in price_ids:
        for env_pid, tier in mapping.items():
            if env_pid and pid == env_pid:
                return tier
    return None


def _subscription_doc_id(project_id: str) -> str:
    return f"sub_{project_id}"


def _record_subscription_state(project_id: str, tier: str, status: str, usage_item: str | None = None):  # pragma: no cover - network side effects
    # Allow explicit usage_item param; fallback to env; then previous cache value
    if not usage_item:
        usage_item = os.getenv("STRIPE_USAGE_SUBSCRIPTION_ITEM") or _subscription_cache.get(project_id, {}).get("usage_item")
    _subscription_cache[project_id] = {"tier": tier, "status": status}
    if usage_item:
        _subscription_cache[project_id]["usage_item"] = usage_item
    client = _firestore_client()
    if client:
        try:
            client.collection("billing_subscriptions").document(_subscription_doc_id(project_id)).set({
                "project_id": project_id,
                "tier": tier,
                "status": status,
                "usage_item": _subscription_cache[project_id].get("usage_item"),
                "updated_at": utc_now().isoformat().replace("+00:00", "Z"),
            })
        except Exception:
            pass


def _load_subscription_state(project_id: str) -> Dict[str, str] | None:
    if project_id in _subscription_cache:
        return _subscription_cache[project_id]
    client = _firestore_client()
    if not client:
        return None
    try:  # pragma: no cover - network
        snap = client.collection("billing_subscriptions").document(_subscription_doc_id(project_id)).get()
        if getattr(snap, "exists", False):
            data = snap.to_dict()  # type: ignore
            if isinstance(data, dict):
                _subscription_cache[project_id] = {
                    "tier": data.get("tier", "free"),
                    "status": data.get("status", "active"),
                }
                if data.get("usage_item"):
                    _subscription_cache[project_id]["usage_item"] = data.get("usage_item")
                return _subscription_cache[project_id]
    except Exception:
        return None
    return None


# --- Metered usage (Stripe UsageRecord) -------------------------------------------

def _maybe_publish_metered_usage(project_id: str, quantity: int):  # pragma: no cover - network
    """Attempt to publish a metered usage record.

    Order of resolution for subscription item (Stripe subscription item id for usage price):
      1. In-memory subscription cache (populated by webhook or prior call) key 'usage_item'
      2. Environment variable STRIPE_USAGE_SUBSCRIPTION_ITEM (static override)
    Fails silently (logged via comment) if insufficient configuration.
    """
    # Fast path: custom test hook
    global _usage_publisher
    if _usage_publisher:
        item = _subscription_cache.get(project_id, {}).get("usage_item") or os.getenv("STRIPE_USAGE_SUBSCRIPTION_ITEM")
        if not item:
            return
        try:
            _usage_publisher(item, quantity, int(utc_now().timestamp()))
        except Exception:
            pass
        return

    if not stripe_configured():
        return
    sub_state = _subscription_cache.get(project_id) or _load_subscription_state(project_id) or {}
    item = sub_state.get("usage_item") or os.getenv("STRIPE_USAGE_SUBSCRIPTION_ITEM")
    if not item:
        return
    try:
        stripe.UsageRecord.create(  # type: ignore
            subscription_item=item,
            quantity=quantity,
            timestamp=int(utc_now().timestamp()),
            action="increment",
        )
    except Exception as e:
        _logger().debug(f"UsageRecord publish failed (non-fatal): {e}")

def inject_usage_publisher(fn: Callable[[str, int, int], None]):  # pragma: no cover - test helper
    global _usage_publisher
    _usage_publisher = fn


# --- Persistent webhook idempotency (Firestore) ----------------------------------

def _persistent_event_seen(event_id: str) -> bool:
    """Return True if event_id already persisted. Swallows all errors.

    Collection: billing_webhook_events
      doc id: event_id
      fields: created_at (ISO), ttl_epoch(optional)
    Optional TTL pruning: if BILLING_WEBHOOK_ID_TTL_SECONDS set, we attach an epoch for
    the desired expiry; Firestore can be configured with TTL on that field or we can
    occasionally prune in-process (best-effort).
    """
    client = _firestore_client()
    if not client:
        return False
    try:  # pragma: no cover - network
        snap = client.collection("billing_webhook_events").document(event_id).get()
        if getattr(snap, "exists", False):
            return True
    except Exception:
        return False
    return False


def _persist_processed_event_id(event_id: str) -> None:
    client = _firestore_client()
    if not client:
        return
    ttl_seconds = _env_int("BILLING_WEBHOOK_ID_TTL_SECONDS", 86400)  # 24h default
    now = utc_now()
    doc = {
        "created_at": now.isoformat().replace("+00:00", "Z"),
    }
    if ttl_seconds > 0:
        try:
            doc["ttl_epoch"] = int(now.timestamp()) + ttl_seconds
        except Exception:
            pass
    try:  # pragma: no cover - network
        client.collection("billing_webhook_events").document(event_id).set(doc, merge=True)
    except Exception:
        # Best effort only
        pass


