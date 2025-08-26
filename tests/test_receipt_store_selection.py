import types
import os
from app import receipts


def test_selects_firestore_store_when_project_and_client(monkeypatch):
    # Simulate FIRESTORE_PROJECT set and available client
    monkeypatch.setenv("FIRESTORE_PROJECT", "demo-proj")

    class DummyClient:  # minimal interface used
        def __init__(self, project):
            self._project = project
        def collection(self, name):  # pragma: no cover - not invoked here
            raise RuntimeError("not used")

    # Patch firestore import symbol inside receipts module (only if attribute missing)
    monkeypatch.setattr(receipts, "firestore", types.SimpleNamespace(Client=lambda project: DummyClient(project)), raising=False)

    store = receipts.load_receipt_store()
    assert isinstance(store, receipts.FirestoreReceiptStore)
    assert store.path.startswith("firestore://demo-proj/")


def test_fallback_to_file_store_on_error(monkeypatch):
    monkeypatch.setenv("FIRESTORE_PROJECT", "demo-proj")

    class ExplodingClient:
        def __init__(self, project):
            raise RuntimeError("boom")

    monkeypatch.setattr(receipts, "firestore", types.SimpleNamespace(Client=lambda project: ExplodingClient(project)), raising=False)
    store = receipts.load_receipt_store()
    # Should have gracefully fallen back
    assert isinstance(store, receipts.ReceiptStore)
