import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app import sft
from app.main import app
from app.utils import canonical_json, sha256_cid

client = TestClient(app)

TEST_SECRETS = {"test-key": "supersecret"}

def _make_mac(payload, payload_type, target_type, trace_id, ts, secret):
    # reproduce server normalization + cid + mac steps
    normalized = sft.normalize(payload, payload_type, target_type)
    normalized_cid = sha256_cid(canonical_json(normalized))
    mac_message = f"{normalized_cid}|{trace_id}|{ts}".encode()
    mac = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), mac_message, hashlib.sha256).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return mac

def test_api_key_missing(monkeypatch):
    monkeypatch.setenv("ODIN_API_KEY_SECRETS", json.dumps(TEST_SECRETS))
    env = {
        "payload": {"a": 1},
        "payload_type": "foo.bar.v1",
        "target_type": "foo.bar.v1",
        "trace_id": "trace-missing",
        "ts": "2024-01-01T00:00:00Z",
    }
    r = client.post("/v1/odin/envelope", json=env)
    assert r.status_code == 401

def test_api_key_invalid_mac(monkeypatch):
    monkeypatch.setenv("ODIN_API_KEY_SECRETS", json.dumps(TEST_SECRETS))
    env = {
        "payload": {"a": 2},
        "payload_type": "foo.bar.v1",
        "target_type": "foo.bar.v1",
        "trace_id": "trace-badmac",
        "ts": "2024-01-01T00:00:10Z",
    }
    headers = {"X-ODIN-API-Key": "test-key", "X-ODIN-API-MAC": "badmac"}
    r = client.post("/v1/odin/envelope", json=env, headers=headers)
    assert r.status_code == 401

def test_api_key_valid(monkeypatch):
    monkeypatch.setenv("ODIN_API_KEY_SECRETS", json.dumps(TEST_SECRETS))
    payload = {"a": 3}
    payload_type = target_type = "foo.bar.v1"
    trace_id = "trace-good"
    ts = "2024-01-01T00:00:20Z"
    mac = _make_mac(payload, payload_type, target_type, trace_id, ts, TEST_SECRETS["test-key"])
    env = {
        "payload": payload,
        "payload_type": payload_type,
        "target_type": target_type,
        "trace_id": trace_id,
        "ts": ts,
    }
    headers = {"X-ODIN-API-Key": "test-key", "X-ODIN-API-MAC": mac}
    r = client.post("/v1/odin/envelope", json=env, headers=headers)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["trace_id"] == trace_id
    assert "receipt" in j
