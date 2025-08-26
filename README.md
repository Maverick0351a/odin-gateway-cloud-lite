# ODIN Gateway Cloud Lite

**The minimal, production-ready starter for verifiable AI→AI communication.**

- FastAPI gateway that accepts *Open Proof Envelopes (OPE)*
- Ed25519 JWKS + response signature headers
- Hash-linked receipt chain with JSONL local storage
- HEL allowlist for optional `forward_url` egress policy
- Signed export bundles for off-box verification
- 1-file Docker image, VS Code tasks, and basic tests

> Use this as the “lite” cloud version of ODIN Gateway for demos, pilots, or as a base to harden.

---

## Quick Start (Local)

```bash
# 0) Clone and enter the project
# git clone <your-repo> odin-gateway-cloud-lite && cd odin-gateway-cloud-lite

# 1) Create a virtual environment & install
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 2) Generate gateway keys
python scripts/gen_keys.py
# copy the printed values into a .env file (or export them)
cp .env.example .env  # Windows: copy .env.example .env
# edit .env and set ODIN_GATEWAY_PRIVATE_KEY_B64 and ODIN_GATEWAY_KID

# 3) Run the gateway (reload for dev)
uvicorn app.main:app --reload --port 8080
```

Smoke test:
```bash
python scripts/demo_send_envelope.py
```

---

## API

- `GET /healthz` (alias `/health`): health info
- `GET /.well-known/jwks.json`: public JWKs (active + additional)
- `POST /v1/odin/envelope`: accept an envelope, create a receipt, return signed response
- `GET /v1/receipts/hops/chain/{trace_id}`: ordered receipt chain
- `GET /v1/receipts/export/{trace_id}`: signed bundle export (CID in header)
 - `GET /v1/billing/usage`: current month usage & tier (experimental)
- `GET /v1/billing/tier`: resolve active tier + limit (free tier only)
 - `POST /v1/billing/checkout`: create Stripe Checkout Session (requires Stripe env vars)
 - `POST /v1/billing/webhook`: Stripe webhook endpoint (signature verified)

### Envelope (lite)
```json
{
  "payload": { "hello": "world" },
  "payload_type": "vendor.event.v1",
  "target_type": "canonical.event.v1",
  "trace_id": "optional",
  "ts": "optional ISO-8601",
  "forward_url": "optional https://postman-echo.com/post"
}
```

### Response headers
- `X-ODIN-Response-CID`
- `X-ODIN-Signature` (Ed25519 over `{cid}|{trace_id}|{ts}`)
- `X-ODIN-KID`

---

## Environment Variables

- `ODIN_GATEWAY_PRIVATE_KEY_B64` (required for signing): base64url Ed25519 seed (32 bytes)
- `ODIN_GATEWAY_KID` (required): key id exposed in JWKS and response headers
- `HEL_ALLOWLIST` (optional): comma-separated hostnames allowed for `forward_url`
- `ODIN_LOCAL_RECEIPTS` (optional): path to JSONL file (default: `./receipts.log.jsonl`)
- `ODIN_RETENTION_MAX_AGE_SECONDS` (optional): prune old receipts on write
- `ODIN_ADDITIONAL_PUBLIC_JWKS` (optional): JSON string with extra/legacy public keys
 - `STRIPE_API_KEY` (optional): enable billing endpoints & Stripe integration
 - `STRIPE_PRICE_PRO` (optional): base subscription price id
 - `STRIPE_PRICE_USAGE` (optional): usage/metered add-on price id
 - `STRIPE_WEBHOOK_SECRET` (optional): used to verify incoming Stripe webhooks
 - `ODIN_BILLING_TIER` (optional): override detected tier (`free`, `pro`, `enterprise`)
 - `FREE_TIER_MONTHLY_RECEIPT_LIMIT` (optional): free tier monthly receipt cap (default 500)
 - `STRIPE_USAGE_SUBSCRIPTION_ITEM` (optional): fallback subscription item id for metered usage if webhook state not yet cached
 - `BILLING_PERSIST_IDEMPOTENCY` (optional): "true" to persist processed Stripe webhook IDs in Firestore for multi-replica idempotency
 - `BILLING_WEBHOOK_ID_TTL_SECONDS` (optional): TTL (seconds) applied to stored webhook IDs (default 86400)
 - `ODIN_REQUEST_LOG_LEVEL` (optional): level for request logging middleware (INFO default)

### Metered Usage

If a usage price (`STRIPE_PRICE_USAGE`) is configured and the active tier resolves to `pro`, `team`, or `enterprise`, each ingested receipt triggers a usage increment published to Stripe (UsageRecord API). The subscription item id is sourced in order of priority:

1. Cached subscription state from webhook events (captured automatically when checkout session completes or subscription events arrive).
2. `STRIPE_USAGE_SUBSCRIPTION_ITEM` environment variable (static fallback / bootstrap).
3. Skipped if neither is available.

Publishing failures are swallowed to avoid impacting ingestion latency. For tests a hook (`billing.inject_usage_publisher`) allows inspection of metered events without Stripe network calls.

### Webhook Idempotency

By default processed Stripe event IDs are cached in-memory (deque, size 500). Set `BILLING_PERSIST_IDEMPOTENCY=true` and configure Firestore to enable cross-replica persistence in collection `billing_webhook_events`. Optional TTL via `BILLING_WEBHOOK_ID_TTL_SECONDS` (24h default). Configure a Firestore TTL policy on `ttl_epoch` or rely on periodic manual cleanup.

---

## Docker

```bash
# Build (defaults to Python 3.13 via build arg in Dockerfile)
docker build -t odin-gateway-cloud-lite:dev .

# Or pin a different supported minor (e.g., 3.11)
docker build --build-arg PYTHON_VERSION=3.11 -t odin-gateway-cloud-lite:py311 .

docker run --rm -p 8080:8080 \
  -e ODIN_GATEWAY_PRIVATE_KEY_B64=... \
  -e ODIN_GATEWAY_KID=gw-dev-001 \
  odin-gateway-cloud-lite:dev
```

---

## VS Code + Copilot

Open the repo in VS Code. Use the **Run and Debug** profile “Run Gateway (Uvicorn)”, or run the tasks:
- *Create venv*
- *Install deps*
- *Run tests*

Then open **`COPILOT.md`** and feed Copilot the prompts to add features (API key HMAC, SFT mapping, Firestore backend, metrics, Cloud Run deploy).

---

## Tests

```bash
python -m pytest -q

# With coverage (used in CI)
python -m pytest -q --cov=app --cov-report=term-missing
```

## Development Environment

Install dev tooling (ruff, pytest, coverage) via `requirements-dev.txt`:

```bash
pip install -r requirements-dev.txt
```

Use `python -m ruff check` before committing (CI will enforce it).

### pre-commit

A `.pre-commit-config.yaml` is provided (ruff + formatting + whitespace + quick pytest):

```bash
pip install -r requirements-dev.txt
pre-commit install
```

Run hooks across all files:

```bash
pre-commit run --all-files
```

## Continuous Integration

GitHub Actions workflow runs on Python 3.11 and 3.13: lint (ruff) + tests with coverage. Adjust matrix in `.github/workflows/ci.yml` as you adopt new runtime versions. The container defaults to Python 3.13; a build arg allows pinning a prior minor.

## Python Version Support

Primary: 3.11 & 3.13 both validated. Container defaults to 3.13 (override with `--build-arg PYTHON_VERSION=3.11`). Keep `pydantic>=2.8,<3` to leverage pre-built wheels on newer versions. Update the CI matrix and Docker build arg if adopting additional versions.

---

## Security Notes

## Cloud Run Deployment

Quick manual deploy (after enabling required APIs and setting PROJECT_ID, REGION):

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com
gcloud artifacts repositories create odin-gateway --repository-format=DOCKER --location=$REGION --description="ODIN Gateway images" || true
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT_ID/odin-gateway/odin-gateway-cloud-lite:manual .
gcloud run deploy odin-gateway-lite \
  --image $REGION-docker.pkg.dev/$PROJECT_ID/odin-gateway/odin-gateway-cloud-lite:manual \
  --region $REGION \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 10 \
  --set-env-vars=ODIN_BILLING_TIER=free
```

Add sensitive env vars via Secret Manager -> Cloud Run (or use `--set-secrets`). Example:

```bash
gcloud secrets create odin-gateway-private-key --data-file=<(echo -n "$ODIN_GATEWAY_PRIVATE_KEY_B64")
gcloud run services update odin-gateway-lite \
  --region $REGION \
  --set-secrets=ODIN_GATEWAY_PRIVATE_KEY_B64=odin-gateway-private-key:latest \
  --set-env-vars=ODIN_GATEWAY_KID=gw-prod-001
```

GitHub Actions deployment is defined in `.github/workflows/deploy.yml` using Workload Identity Federation (preferred). Provide secrets:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT_EMAIL`
- Optional: `ODIN_BILLING_TIER` (default free)

Firestore (optional) initialization (native / in the same project):

```bash
gcloud firestore databases create --location=nam5 || true
```

Create Stripe secrets:

```bash
gcloud secrets create stripe-api-key --data-file=<(echo -n "$STRIPE_API_KEY")
gcloud secrets create stripe-webhook-secret --data-file=<(echo -n "$STRIPE_WEBHOOK_SECRET")
gcloud run services update odin-gateway-lite \
  --region $REGION \
  --set-secrets=STRIPE_API_KEY=stripe-api-key:latest,STRIPE_WEBHOOK_SECRET=stripe-webhook-secret:latest \
  --set-env-vars=STRIPE_PRICE_PRO=price_xxx,STRIPE_PRICE_USAGE=price_yyy
```

Enable persistent webhook idempotency:

```bash
gcloud run services update odin-gateway-lite \
  --region $REGION \
  --set-env-vars=BILLING_PERSIST_IDEMPOTENCY=true,BILLING_WEBHOOK_ID_TTL_SECONDS=86400
```

Confirm health:

```bash
curl -s $(gcloud run services describe odin-gateway-lite --region $REGION --format='value(status.url)')/healthz | jq
```

### Local MAC Helper Script
Generate the required HMAC for an ingestion request (mirrors server logic):

```bash
python scripts/compute_mac.py \
  --api-key demo \
  --secret supersecret \
  --payload-file sample_payload.json \
  --payload-type example_input \
  --target-type model_v1 > mac.json

cat mac.json
```

Then send (example):

```bash
CID=$(jq -r .cid mac.json)
TRACE=$(jq -r .trace mac.json)
TS=$(jq -r .ts mac.json)
MAC=$(jq -r .mac mac.json)
API_KEY=$(jq -r .api_key mac.json)
curl -X POST "$URL/v1/odin/envelope" \
  -H "X-Odin-Api-Key: $API_KEY" \
  -H "X-Odin-Api-Mac: $MAC" \
  -H "Content-Type: application/json" \
  -d @<(jq -n --argfile p sample_payload.json --arg pt example_input --arg tt model_v1 --arg tr $TRACE --arg ts $TS '{payload:$p,payload_type:$pt,target_type:$tt,trace_id:$tr,ts:$ts}'))
```

- Keep `ODIN_GATEWAY_PRIVATE_KEY_B64` out of source control; use secrets or env.
- For real deployments, run behind HTTPS and configure a proper egress policy (HEL).
- Add API key + HMAC auth for defense-in-depth (see `COPILOT.md`).
 - Treat `STRIPE_API_KEY` & webhook secret as sensitive; prefer secret managers in production.

---

## License

Apache-2.0
