import json
import os

import httpx

GATEWAY = os.getenv("GATEWAY_URL", "http://127.0.0.1:8080")
payload = {
    "tool_calls": [
        {
            "type": "function",
            "function": {
                "name": "create_invoice",
                "arguments": (
                    "{\"invoice_id\":\"INV-1\",\"amount\":99.5,"
                    "\"currency\":\"USD\", \"customer_name\":\"Acme\"}"
                ),
            },
        }
    ],
    "created_at": "2025-01-01T00:00:00Z",
}

env = {
    "payload": payload,
    "payload_type": "openai.tooluse.invoice.v1",
    "target_type": "invoice.iso20022.v1",
    # "forward_url":"https://postman-echo.com/post"
}
with httpx.Client(timeout=10.0) as client:
    r = client.post(f"{GATEWAY}/v1/odin/envelope", json=env)
    print("Status:", r.status_code)
    print("Headers:", {k:v for k,v in r.headers.items() if k.lower().startswith("x-odin")})
    data = r.json()
    print(json.dumps(data, indent=2))
    tid = data["trace_id"]
    r2 = client.get(f"{GATEWAY}/v1/receipts/hops/chain/{tid}")
    print("Chain:", json.dumps(r2.json(), indent=2))
    r3 = client.get(f"{GATEWAY}/v1/receipts/export/{tid}")
    print("Export:", json.dumps(r3.json(), indent=2))
