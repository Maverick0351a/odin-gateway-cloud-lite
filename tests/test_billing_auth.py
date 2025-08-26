import json
from fastapi.testclient import TestClient
from app.main import app

def test_billing_usage_requires_key(monkeypatch):
    # configure API keys
    monkeypatch.setenv('ODIN_API_KEY_SECRETS', json.dumps({'k1':'secret'}))
    client = TestClient(app)
    # missing key
    r = client.get('/v1/billing/usage')
    assert r.status_code == 401
    # wrong key
    r = client.get('/v1/billing/usage', headers={'X-ODIN-API-Key':'bad'})
    assert r.status_code == 401
    # correct key
    r = client.get('/v1/billing/usage', headers={'X-ODIN-API-Key':'k1'})
    assert r.status_code == 200
    body = r.json()
    assert 'usage' in body and 'tier' in body
