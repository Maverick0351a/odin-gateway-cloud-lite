from __future__ import annotations

import datetime
import json
import os
from typing import Any

from .utils import canonical_json, sha256_cid

# Optional Firestore support (lazy import)
_firestore_unavailable_reason: str | None = None
try:
    if os.getenv("FIRESTORE_PROJECT"):
        from google.cloud import firestore  # type: ignore
except Exception as e:  # pragma: no cover - import guard
    _firestore_unavailable_reason = str(e)

class ReceiptStore:
    def __init__(self, path: str | None = None, max_age_seconds: int | None = None):
        self.path = path or os.getenv("ODIN_LOCAL_RECEIPTS") or "receipts.log.jsonl"
        try:
            env_val = os.getenv("ODIN_RETENTION_MAX_AGE_SECONDS")
            self.max_age_seconds = int(env_val or (max_age_seconds or 0))
        except Exception:
            self.max_age_seconds = 0

    def _read_lines(self) -> list[str]:
        try:
            with open(self.path, encoding="utf-8") as f:
                return [ln for ln in f.read().splitlines() if ln.strip()]
        except FileNotFoundError:
            return []

    def _write_lines(self, lines: list[str]) -> None:
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))

    def _prune_by_age(self, lines: list[str]) -> list[str]:
        if not self.max_age_seconds or self.max_age_seconds <= 0:
            return lines
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            seconds=self.max_age_seconds
        )
        kept: list[str] = []
        for ln in lines:
            try:
                obj = json.loads(ln)
                ts = datetime.datetime.fromisoformat(obj.get("ts"))
                if ts >= cutoff:
                    kept.append(ln)
            except Exception:
                kept.append(ln)
        return kept

    def add(self, r: dict[str, Any]) -> dict[str, Any]:
        lines = self._read_lines()
        # prune before append
        lines = self._prune_by_age(lines)
        # compute receipt hash and link
        prev_hash = json.loads(lines[-1]).get("receipt_hash") if lines else None
        r2 = dict(r)
        r2["prev_receipt_hash"] = prev_hash
        # receipt hash excludes 'receipt_hash' field itself
        rh = sha256_cid(
            canonical_json({k: v for k, v in r2.items() if k != "receipt_hash"})
        )
        r2["receipt_hash"] = rh
        lines.append(json.dumps(r2, ensure_ascii=False))
        self._write_lines(lines)
        return r2

    def chain(self, trace_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ln in self._read_lines():
            try:
                obj = json.loads(ln)
                if obj.get("trace_id") == trace_id:
                    out.append(obj)
            except Exception:
                continue
        # order by hop or ts then return
        out.sort(key=lambda x: (x.get("hop", 0), x.get("ts", "")))
        return out


class FirestoreReceiptStore:
    """Firestore-backed receipt store.

    Collection structure:
      collection: receipts
        doc auto id with fields of receipt including trace_id

    We keep the same receipt hash linking semantics as file store. For efficiency
    we just fetch the last receipt to compute prev hash (ordered by created ts) and
    query by trace_id for chains.
    """
    def __init__(self, project_id: str, collection: str = "receipts"):
        if _firestore_unavailable_reason:
            raise RuntimeError(f"Firestore not available: {_firestore_unavailable_reason}")
        # client will use ADC or env credentials
        self.client = firestore.Client(project=project_id)  # type: ignore
        self.collection = collection
        self.path = f"firestore://{project_id}/{collection}"  # mimic file path attr

    def _collection(self):  # small indirection for tests/mocking
        return self.client.collection(self.collection)

    def _latest_receipt_hash(self) -> str | None:
        try:
            # fetch latest by ts descending, limit 1
            query = (
                self._collection()
                .order_by("ts", direction=firestore.Query.DESCENDING)
                .limit(1)
            )
            docs = list(query.stream())  # type: ignore
            if docs:
                return docs[0].to_dict().get("receipt_hash")
        except Exception:
            return None
        return None

    def add(self, r: dict[str, Any]) -> dict[str, Any]:
        prev_hash = self._latest_receipt_hash()
        r2 = dict(r)
        r2["prev_receipt_hash"] = prev_hash
        rh = sha256_cid(canonical_json({k: v for k, v in r2.items() if k != "receipt_hash"}))
        r2["receipt_hash"] = rh
        try:
            self._collection().add(r2)
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"Failed to add receipt to Firestore: {e}")
        return r2

    def chain(self, trace_id: str) -> list[dict[str, Any]]:
        try:
            q = self._collection().where("trace_id", "==", trace_id)
            docs = list(q.stream())
            out = [d.to_dict() for d in docs]
        except Exception:
            out = []
        out.sort(key=lambda x: (x.get("hop", 0), x.get("ts", "")))
        return out


def load_receipt_store() -> ReceiptStore | FirestoreReceiptStore:
    project = os.getenv("FIRESTORE_PROJECT")
    if project:
        try:
            return FirestoreReceiptStore(project)
        except Exception:
            # fall back to local store if Firestore misconfigured
            return ReceiptStore()
    base_store: ReceiptStore | FirestoreReceiptStore = ReceiptStore()
    # Optional caching wrapper
    if os.getenv("ODIN_RECEIPT_CACHE", "").lower() in {"1", "true", "yes", "on"}:
        try:
            return CachingReceiptStore(base_store)
        except Exception:  # pragma: no cover - cache init should not break core path
            return base_store
    return base_store


class CachingReceiptStore:
    """A lightweight in-memory caching layer for receipt stores.

    Goals:
      * Avoid repeated disk / Firestore reads for frequent chain(trace_id) calls.
      * Cache recently added receipts to accelerate subsequent queries.

    Simplifications:
      * Cache is per-process only (not shared across replicas).
      * No persistence beyond process lifetime.
      * Basic TTL & size cap controlled by env vars.

    Env vars:
      * ODIN_RECEIPT_CACHE_TTL_SECONDS (default: 300)
      * ODIN_RECEIPT_CACHE_SIZE (approx max receipts cached, default: 1000)
    """

    def __init__(self, store: ReceiptStore | FirestoreReceiptStore):
        self.store = store
        try:
            self.ttl_seconds = int(os.getenv("ODIN_RECEIPT_CACHE_TTL_SECONDS", "300"))
        except Exception:  # pragma: no cover - fallback
            self.ttl_seconds = 300
        try:
            self.max_size = int(os.getenv("ODIN_RECEIPT_CACHE_SIZE", "1000"))
        except Exception:  # pragma: no cover
            self.max_size = 1000
        self._by_trace: dict[str, dict[str, Any]] = {}
        # maps trace_id -> list[receipt]
        self._chains: dict[str, list[dict[str, Any]]] = {}
        self._last_refresh: dict[str, datetime.datetime] = {}
        self._total_cached = 0

    def _expired(self, trace_id: str) -> bool:
        ts = self._last_refresh.get(trace_id)
        if not ts:
            return True
        if self.ttl_seconds <= 0:
            return False
        return (datetime.datetime.now(datetime.UTC) - ts).total_seconds() > self.ttl_seconds

    def _enforce_size(self) -> None:
        if self.max_size <= 0:
            return
        if self._total_cached <= self.max_size:
            return
        # simple LRU-ish eviction based on oldest refresh time
        ordered = sorted(self._last_refresh.items(), key=lambda x: x[1])
        for trace_id, _ in ordered:
            removed = len(self._chains.get(trace_id, []))
            self._chains.pop(trace_id, None)
            self._last_refresh.pop(trace_id, None)
            self._total_cached -= removed
            if self._total_cached <= self.max_size:
                break

    # Public API mirrors underlying subset
    def add(self, r: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        added = getattr(self.store, "add")(r)
        trace_id = added.get("trace_id")
        if trace_id:
            # invalidate chain cache for this trace_id to rebuild lazily
            self._chains.pop(trace_id, None)
            self._last_refresh.pop(trace_id, None)
        return added

    def chain(self, trace_id: str) -> list[dict[str, Any]]:  # type: ignore[override]
        if trace_id in self._chains and not self._expired(trace_id):
            return self._chains[trace_id]
        fresh = getattr(self.store, "chain")(trace_id)
        self._chains[trace_id] = fresh
        self._last_refresh[trace_id] = datetime.datetime.now(datetime.UTC)
        # recompute total
        self._total_cached = sum(len(v) for v in self._chains.values())
        self._enforce_size()
        return fresh

    # For transparency/debug
    def cache_stats(self) -> dict[str, Any]:  # pragma: no cover - debug utility
        return {
            "trace_ids": len(self._chains),
            "receipts": self._total_cached,
            "ttl_seconds": self.ttl_seconds,
            "max_size": self.max_size,
        }
