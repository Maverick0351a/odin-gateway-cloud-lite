from __future__ import annotations

from typing import Any

"""Simple SFT (Structured Format Transform) normalization utilities.

Supported mappings:
  openai.tooluse.invoice.v1 -> invoice.iso20022.v1
  invoice.vendor.v1         -> invoice.iso20022.v1

We only map a handful of common invoice fields for demonstration.
"""


def _to_iso20022_invoice(payload: dict[str, Any]) -> dict[str, Any]:
    # canonical field extraction with flexible aliases
    def first(*names: str):
        for n in names:
            if n in payload and payload[n] is not None:
                return payload[n]
        return None

    total = first("total", "amount_total", "gross_amount", "amount")
    currency = first("currency", "ccy", "iso_currency") or "USD"
    invoice_id = first("invoice_id", "id", "number")
    supplier = first("supplier", "vendor", "from")
    customer = first("customer", "to", "recipient")
    issue_date = first("issue_date", "date", "created_at")

    lines = payload.get("lines") or payload.get("items") or []
    norm_lines = []
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        norm_lines.append({
            "description": ln.get("description") or ln.get("name"),
            "quantity": ln.get("quantity") or ln.get("qty") or 1,
            "unit_price": ln.get("unit_price") or ln.get("price") or ln.get("unitPrice"),
            "total": ln.get("total") or ln.get("line_total") or ln.get("amount"),
        })

    return {
        "type": "invoice.iso20022.v1",
        "invoice_id": invoice_id,
        "currency": currency,
        "total": total,
        "issue_date": issue_date,
        "supplier": supplier,
        "customer": customer,
        "lines": norm_lines,
        "raw_source": payload,  # keep original for traceability
    }


def normalize(payload: dict[str, Any], payload_type: str, target_type: str) -> dict[str, Any]:
    if target_type == "invoice.iso20022.v1" and payload_type in (
        "openai.tooluse.invoice.v1",
        "invoice.vendor.v1",
    ):
        return _to_iso20022_invoice(payload)
    # identity fallback
    return payload
