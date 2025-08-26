from fastapi.testclient import TestClient

from app import billing as billing_module
from app.main import app

client = TestClient(app)

def test_health_and_jwks():
    r = client.get("/healthz")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True

    # Without env signer set, jwks may be empty
    rj = client.get("/.well-known/jwks.json").json()
    assert "keys" in rj

def test_envelope_and_chain(monkeypatch):
    # set fake signer env and reload app signer if needed
    # (here we rely on process env set before import for simplicity)
    env = {
        "payload":{"hello":"world"},
        "payload_type":"vendor.event.v1",
        "target_type":"canonical.event.v1"
    }
    r = client.post("/v1/odin/envelope", json=env)
    assert r.status_code == 200
    resp = r.json()
    tid = resp["trace_id"]
    assert "receipt" in resp

    chain = client.get(f"/v1/receipts/hops/chain/{tid}").json()
    assert chain["trace_id"] == tid
    assert len(chain["chain"]) >= 1

    exp = client.get(f"/v1/receipts/export/{tid}")
    assert exp.status_code == 200
    assert "X-ODIN-Response-CID" in exp.headers


def test_forward_blocked(monkeypatch):
    # configure HEL allowlist to NOT include blocked.example.com
    monkeypatch.setenv("HEL_ALLOWLIST", "allowed.example.com")
    env = {
        "payload": {"hello": "world"},
        "payload_type": "openai.tooluse.invoice.v1",
        "target_type": "invoice.iso20022.v1",
        "forward_url": "https://blocked.example.com/hook"
    }
    r = client.post("/v1/odin/envelope", json=env)
    assert r.status_code == 403


def test_receipt_chain_cache(monkeypatch, tmp_path):
    # Force local file store (avoid Firestore) and enable cache
    monkeypatch.delenv('FIRESTORE_PROJECT', raising=False)
    monkeypatch.setenv('ODIN_LOCAL_RECEIPTS', str(tmp_path / 'cache_test.log'))
    monkeypatch.setenv('ODIN_RECEIPT_CACHE', '1')
    from app import receipts as receipts_module
    store = receipts_module.load_receipt_store()
    trace_id = 'cache-trace'
    store.add({'trace_id': trace_id, 'ts': '2025-01-01T00:00:00Z', 'hop': 1})
    store.add({'trace_id': trace_id, 'ts': '2025-01-01T00:00:01Z', 'hop': 2})
    first = store.chain(trace_id)
    second = store.chain(trace_id)  # should be served from cache
    assert first == second
    assert len(second) == 2
    assert [r['hop'] for r in second] == [1, 2]


def test_free_tier_quota(monkeypatch):
    # Reset billing usage state
    billing_module._usage_cache.clear()
    billing_module._usage_cache_month = None
    # Force free tier with limit 1
    monkeypatch.setenv("ODIN_BILLING_TIER", "free")
    monkeypatch.setenv("FREE_TIER_MONTHLY_RECEIPT_LIMIT", "1")
    from app.main import app as appref
    local_client = TestClient(appref)
    env = {
        "payload": {"x": 1},
        "payload_type": "vendor.event.v1",
        "target_type": "canonical.event.v1",
    }
    r1 = local_client.post("/v1/odin/envelope", json=env)
    assert r1.status_code == 200
    r2 = local_client.post("/v1/odin/envelope", json=env)
    assert r2.status_code == 402
