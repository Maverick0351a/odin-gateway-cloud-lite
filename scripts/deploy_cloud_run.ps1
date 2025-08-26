<#
Unified Cloud Run deployment script.

Supports two build modes:
  1. Cloud Build (default) using gcloud builds submit
  2. Local docker build + push (set -UseLocalDocker)

Env var sources (merged in order, later wins):
  * --EnvVars KEY=VALUE pairs
  * --EnvFile path/to/file (.env style)
  * ./.env (auto if present, unless -NoDotEnv)

Secrets: Provide names via -SecretVars KEY=secret-name (Secret Manager) to map
         to container env using --set-secrets KEY=secret-name:latest
Example:
  ./deploy_cloud_run.ps1 -Project myproj -Region us-central1 -Service odin-gw `
     -CreateRepo -EnvVars ODIN_GATEWAY_KID=gw-prod-001,BILLING_PERSIST_IDEMPOTENCY=true `
     -SecretVars STRIPE_API_KEY=stripe-api-key,ODIN_API_KEY_SECRETS=odin-api-keys
#>
param(
  [Parameter(Mandatory=$true)][string]$Project,
  [string]$Region = "us-central1",
  [string]$Service = "odin-gateway-lite",
  [string]$Repo = "odin-gateway",
  [string]$ImageTag = "v$(Get-Date -Format yyyyMMddHHmmss)",
  [switch]$CreateRepo,
  [switch]$SkipBuild,
  [switch]$UseLocalDocker,
  [string[]]$EnvVars = @(),
  [string[]]$SecretVars = @(),   # KEY=secretName
  [string]$EnvFile,
  [switch]$NoDotEnv,
  [int]$MinInstances = 0,
  [int]$MaxInstances = 10,
  [string]$CPU = "1",
  [string]$Memory = "512Mi",
  [string]$ServiceAccount,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Write-Host "[Init] Project=$Project Region=$Region Service=$Service Repo=$Repo ImageTag=$ImageTag"

gcloud config set project $Project | Out-Null
Write-Host '[Enable] Ensuring required services...'
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com firestore.googleapis.com | Out-Null

if ($CreateRepo) {
  Write-Host "[Repo] Creating Artifact Registry repo '$Repo' (ignore Already exists)..."
  try {
    gcloud artifacts repositories create $Repo --repository-format=DOCKER --location=$Region --description 'ODIN Gateway images' | Out-Null
  } catch {
    Write-Host '[Repo] Exists or error (continuing)'
  }
}

$ImageRef = "$Region-docker.pkg.dev/$Project/$Repo/gateway:$ImageTag"
if (-not $SkipBuild) {
  if ($UseLocalDocker) {
    Write-Host "[Build] Local docker build $ImageRef"
    docker build -t $ImageRef .
    Write-Host '[Auth] Configuring docker for Artifact Registry'
    gcloud auth configure-docker $Region-docker.pkg.dev -q
    Write-Host '[Push] Pushing image'
    docker push $ImageRef
  } else {
    Write-Host "[Build] Cloud Build submit $ImageRef"
    gcloud builds submit --tag $ImageRef .
  }
} else {
  Write-Host '[Build] Skipped'
}

# Collect env vars
$SetEnv = @()
function Add-EnvPair([string]$Raw) {
  if (-not $Raw) { return }
  if ($Raw -notmatch '=') { Write-Warning "Ignoring env pair without '=': $Raw"; return }
  $SetEnv += $Raw
}

if (-not $NoDotEnv -and (Test-Path .env)) {
  Write-Host '[Env] Loading ./.env'
  Get-Content .env | ForEach-Object { if ($_ -and $_ -notmatch '^#') { Add-EnvPair $_ } }
}
if ($EnvFile) {
  if (-not (Test-Path $EnvFile)) { Write-Error "EnvFile not found: $EnvFile"; exit 1 }
  Write-Host "[Env] Loading $EnvFile"
  Get-Content $EnvFile | ForEach-Object { if ($_ -and $_ -notmatch '^#') { Add-EnvPair $_ } }
}
foreach ($ev in $EnvVars) { Add-EnvPair $ev }

# Defaults (ensure logging format variable explicit if desired)
if (-not ($SetEnv -match '^ODIN_REQUEST_LOG_LEVEL=')) { Add-EnvPair 'ODIN_REQUEST_LOG_LEVEL=INFO' }

$EnvArgs = @()
if ($SetEnv.Count -gt 0) {
  $EnvArgs += '--set-env-vars'
  $EnvArgs += ($SetEnv -join ',')
}

# Secrets mapping
$SecretArgs = @()
foreach ($sv in $SecretVars) {
  if ($sv -match '=') {
    $k,$sec = $sv.Split('=',2)
    $SecretArgs += '--set-secrets'
    $SecretArgs += "$k=$sec:latest"
  } else {
    Write-Warning "Ignoring secret mapping without '=': $sv"
  }
}

if ($DryRun) {
  Write-Host '[DryRun] Deployment command would be:'
  Write-Host "gcloud run deploy $Service --image $ImageRef --region $Region --allow-unauthenticated --cpu $CPU --memory $Memory --max-instances $MaxInstances --min-instances $MinInstances $EnvArgs $SecretArgs"
  exit 0
}

Write-Host '[Deploy] Deploying service...'
$DeployCmd = @(
  'run','deploy', $Service,
  '--image', $ImageRef,
  '--region', $Region,
  '--allow-unauthenticated',
  '--cpu', $CPU,
  '--memory', $Memory,
  '--max-instances', $MaxInstances,
  '--min-instances', $MinInstances
)
if ($ServiceAccount) { $DeployCmd += @('--service-account', $ServiceAccount) }
if ($EnvArgs) { $DeployCmd += $EnvArgs }
if ($SecretArgs) { $DeployCmd += $SecretArgs }

gcloud @DeployCmd

Write-Host '[Info] Fetching service URL'
$Url = (gcloud run services describe $Service --region $Region --format 'value(status.url)')
if (-not $Url) { Write-Error 'Could not determine service URL'; exit 1 }
Write-Host "[Info] Service URL: $Url"

Write-Host '[Smoke] /healthz'
try { (Invoke-WebRequest -UseBasicParsing "$Url/healthz").Content | Write-Host } catch { Write-Warning $_ }
Write-Host '[Smoke] /.well-known/jwks.json'
try { (Invoke-WebRequest -UseBasicParsing "$Url/.well-known/jwks.json").Content | Write-Host } catch { Write-Warning $_ }

Write-Host '[Done] Deployment complete.'