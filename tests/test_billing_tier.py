from fastapi.testclient import TestClient
from app.main import app
from app import billing

def test_billing_tier_free(monkeypatch):
    monkeypatch.setenv("ODIN_BILLING_TIER", "free")
    client = TestClient(app)
    # No API key configured => open
    r = client.get("/v1/billing/tier")
    assert r.status_code == 200
    data = r.json()
    assert data["tier"] == "free"
    assert data["limit"] is not None


def test_billing_tier_with_api_key(monkeypatch):
    monkeypatch.setenv("ODIN_API_KEY_SECRETS", '{"k1":"s"}')
    monkeypatch.setenv("ODIN_BILLING_TIER", "pro")
    client = TestClient(app)
    r = client.get("/v1/billing/tier", headers={"X-ODIN-API-Key":"k1"})
    assert r.status_code == 200
    assert r.json()["tier"] == "pro"


def test_webhook_idempotent(monkeypatch):
    # Simulate two identical events; second should be idempotent
    from app import billing as bm
    ev = type("Evt", (), {"type":"checkout.session.completed","id":"evt_123","data":{"object":{"metadata":{"project_id":"p1"},"id":"sess_1"}}})
    # Mock retrieve
    class LineItem:
        def __init__(self, price_id, item_id):
            self.price = type("Price",(),{"id":price_id})
            self.id = item_id
    class Session:
        def __init__(self):
            self.line_items = type("LI",(),{"data":[LineItem("price_pro", "li_1")]})
    monkeypatch.setattr(bm.stripe.checkout.Session, "retrieve", lambda sid, expand=None: Session())
    monkeypatch.setenv("STRIPE_PRICE_PRO","price_pro")
    first = bm.handle_webhook_event(ev)
    second = bm.handle_webhook_event(ev)
    assert first["idempotent"] is False
    assert second["idempotent"] is True
