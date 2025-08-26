from app import receipts


def test_firestore_store_add_and_chain(monkeypatch):
    # Use stubbed Firestore client; avoids network while exercising logic
    monkeypatch.setenv("FIRESTORE_PROJECT", "demo-project")

    # Build a stub collection/query system
    storage = []

    class StubDoc:
        def __init__(self, data):
            self._data = data
        def to_dict(self):
            return self._data

    class StubWhereQuery:
        def __init__(self, trace_id):
            self.trace_id = trace_id
        def stream(self):
            return [StubDoc(d) for d in storage if d.get("trace_id") == self.trace_id]

    class StubCollection:
        def add(self, data):
            storage.append(data)
            return (None, None)
        def where(self, field, op, value):  # noqa: D401 (simple stub)
            assert field == "trace_id" and op == "=="
            return StubWhereQuery(value)
        # Methods used only by _latest_receipt_hash; override that method instead

    # Monkeypatch FirestoreReceiptStore init to bypass real client, then override helpers
    def fake_init(self, project_id: str, collection: str = "receipts"):
        self.client = None
        self.collection = collection
        self.path = f"firestore://{project_id}/{collection}"
    monkeypatch.setattr(receipts.FirestoreReceiptStore, "__init__", fake_init)
    monkeypatch.setattr(
        receipts.FirestoreReceiptStore,
        "_collection",
        lambda self: StubCollection(),
    )
    monkeypatch.setattr(receipts.FirestoreReceiptStore, "_latest_receipt_hash", lambda self: None)

    store = receipts.load_receipt_store()
    # Ensure we actually got the Firestore variant
    assert isinstance(store, receipts.FirestoreReceiptStore)

    r1 = store.add({"trace_id": "t1", "hop": 0, "ts": "2024-01-01T00:00:00Z"})
    assert r1.get("receipt_hash")
    store.add({"trace_id": "t1", "hop": 1, "ts": "2024-01-01T00:00:10Z"})
    chain = store.chain("t1")
    assert len(chain) == 2
    assert chain[0]["hop"] == 0 and chain[1]["hop"] == 1
