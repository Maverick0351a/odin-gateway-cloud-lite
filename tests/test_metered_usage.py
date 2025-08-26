import os
from app import billing

def test_injected_usage_publisher(monkeypatch):
    # Force tier to pro via subscription cache
    project_id = "proj-test"
    billing._record_subscription_state(project_id, tier="pro", status="active", usage_item="sub_item_123")  # type: ignore

    published = []
    def fake_pub(item, qty, ts):
        published.append((item, qty, ts))

    billing.inject_usage_publisher(fake_pub)
    # Simulate several receipts
    for _ in range(3):
        billing.record_receipt(project_id, metered=True)
    # Expect 3 increments captured
    assert len(published) == 3
    assert all(p[0] == "sub_item_123" and p[1] == 1 for p in published)
    # timestamps should be integers
    assert all(isinstance(p[2], int) for p in published)
