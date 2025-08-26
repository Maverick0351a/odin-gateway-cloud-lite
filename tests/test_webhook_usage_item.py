import types
import os

from app import billing


def test_checkout_session_completed_usage_item(monkeypatch):
    # Ensure automatic tier detection (not forced override)
    monkeypatch.delenv("ODIN_BILLING_TIER", raising=False)

    # Provide pricing environment variables
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro_123")
    monkeypatch.setenv("STRIPE_PRICE_USAGE", "price_usage_456")

    project_id = "proj-test-usage"

    # Build fake line item objects mimicking Stripe's shape
    class Price:
        def __init__(self, id):
            self.id = id

    class LineItem:
        def __init__(self, id, price_id):
            self.id = id
            self.price = Price(price_id)

    usage_item_id = "si_usage_789"

    session_obj = types.SimpleNamespace(
        line_items=types.SimpleNamespace(
            data=[
                LineItem("si_sub_base", os.getenv("STRIPE_PRICE_PRO")),
                LineItem(usage_item_id, os.getenv("STRIPE_PRICE_USAGE")),
            ]
        )
    )

    # Stub stripe.checkout.Session.retrieve
    class FakeSessionAPI:
        @staticmethod
        def retrieve(sid, expand=None):  # noqa: D401
            assert sid == "sess_123"
            assert expand == ["line_items"]
            return session_obj

    fake_stripe = types.SimpleNamespace(checkout=types.SimpleNamespace(Session=FakeSessionAPI))
    monkeypatch.setattr(billing, "stripe", fake_stripe)

    # Build event object structure expected by handler
    event = types.SimpleNamespace(
        type="checkout.session.completed",
        data={
            "object": {
                "id": "sess_123",
                "metadata": {"project_id": project_id},
            }
        },
    )

    result = billing.handle_webhook_event(event)

    assert result["tier_changed"] is True
    assert result["project_id"] == project_id
    # Subscription cache should now have usage item recorded
    sub_state = billing._subscription_cache.get(project_id)  # type: ignore[attr-defined]
    assert sub_state is not None
    assert sub_state.get("usage_item") == usage_item_id
