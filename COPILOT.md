# Copilot Guide: ODIN Gateway Cloud Lite

Use these prompts in VS Code Copilot Chat to extend and maintain this gateway.

## 1) Add API key + HMAC (defense-in-depth)
> Add optional API key auth to `POST /v1/odin/envelope`. When `ODIN_API_KEY_SECRETS` is set to a JSON of key->secret, require headers `X-ODIN-API-Key` and `X-ODIN-API-MAC` (base64url of HMAC-SHA256 over `"{cid}|{trace_id}|{ts}"`). Return 401 on missing/invalid.

## 2) Add SFT mapping (payload normalization)
> Create `app/sft.py` with a function `normalize(payload, payload_type, target_type)` that supports:
> - `openai.tooluse.invoice.v1` -> `invoice.iso20022.v1` (map basic fields)
> - `invoice.vendor.v1` -> `invoice.iso20022.v1`
> Import and use it in `POST /v1/odin/envelope` so `normalized` is the mapped structure.

## 3) Persist receipts to Firestore (optional backend)
> Add an alternative store class `FirestoreReceiptStore(project_id)` and inject it when `FIRESTORE_PROJECT` is set. Keep JSONL as default.

## 4) Metrics
> Add `/metrics` endpoint with Prometheus exposition (Counter for requests; Histogram for latency). Guard duplicates during tests.

## 5) Cloud Run Deploy
> Create `scripts/deploy_cloud_run.ps1` that builds the Docker image, pushes to Artifact Registry, and deploys a Cloud Run service with env vars from `.env` (e.g., gateway KID/seed, HEL allowlist). Include smoke checks for `/healthz` and `/.well-known/jwks.json`.

## 6) End-to-end test
> In `tests/test_gateway.py`, add a test that sets env variables via monkeypatch for signer and HEL allowlist, then verifies that an envelope with a disallowed forward_url returns 403.

