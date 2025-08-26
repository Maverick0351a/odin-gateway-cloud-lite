from app import sft


def test_invoice_normalization_openai_tooluse():
    payload = {
        "invoice_id": "INV-123",
        "total": 123.45,
        "currency": "EUR",
        "supplier": "Acme GmbH",
        "customer": "Buyer AG",
        "lines": [
            {"description": "Widget", "quantity": 2, "unit_price": 10, "total": 20},
            {"name": "Service", "qty": 1, "price": 103.45, "amount": 103.45},
        ],
    }
    norm = sft.normalize(payload, "openai.tooluse.invoice.v1", "invoice.iso20022.v1")
    assert norm["type"] == "invoice.iso20022.v1"
    assert norm["invoice_id"] == "INV-123"
    assert norm["currency"] == "EUR"
    assert norm["total"] == 123.45
    assert len(norm["lines"]) == 2
    assert norm["lines"][0]["description"] == "Widget"
    assert norm["lines"][1]["description"] == "Service"


def test_invoice_normalization_vendor_invoice():
    payload = {
        "id": "V-9",
        "amount_total": 50,
        "ccy": "USD",
        "vendor": "Supplier Co",
        "to": "Customer Co",
        "items": [
            {"name": "Thing", "qty": 5, "unit_price": 5, "line_total": 25},
            {"name": "Other", "qty": 1, "price": 25, "amount": 25},
        ],
    }
    norm = sft.normalize(payload, "invoice.vendor.v1", "invoice.iso20022.v1")
    assert norm["invoice_id"] == "V-9"
    assert norm["total"] == 50
    assert norm["supplier"] == "Supplier Co"
    assert norm["customer"] == "Customer Co"
    assert len(norm["lines"]) == 2


def test_identity_fallback():
    payload = {"foo": "bar"}
    norm = sft.normalize(payload, "foo.bar.v1", "foo.bar.v1")
    assert norm is payload
