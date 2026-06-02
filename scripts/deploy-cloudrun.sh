#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy-cloudrun.sh — Deploy a CCFoundry agent to Google Cloud Run
#
# Usage:
#   ./scripts/deploy-cloudrun.sh <agent-name> [options]
#
# Options:
#   --project <id>       GCP project ID (defaults to active gcloud project)
#   --region <region>    Cloud Run region (default: europe-west2)
#   --agent-space <path> Path to agent_space directory to deploy
#   --min-instances <n>  Minimum instances (default: 0 = scale-to-zero)
#   --memory <size>      Memory allocation (default: 512Mi)
#   --cpu <n>            CPU allocation   (default: 1)
#   --poll-schedule <s>  Cloud Scheduler cron expression (default: "* * * * *")
#   --foundry-url <url>  Foundry base URL to register the agent with
#   --skip-scheduler     Don't create a Cloud Scheduler job
#   --dry-run            Print commands without executing
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────────
GCP_PROJECT="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-${CLOUDSDK_CORE_PROJECT:-}}}"
GCP_REGION="europe-west2"
AR_REPO="ccfoundry-agents"
AGENT_SPACE=""
MIN_INSTANCES=0
MEMORY="512Mi"
CPU="1"
POLL_SCHEDULE="* * * * *"
FOUNDRY_URL=""
SKIP_SCHEDULER=false
DRY_RUN=false

# ── Parse arguments ───────────────────────────────────────────────────────────
AGENT_NAME="${1:-}"
shift || true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)       GCP_PROJECT="$2"; shift 2 ;;
        --region)        GCP_REGION="$2"; shift 2 ;;
        --agent-space)   AGENT_SPACE="$2"; shift 2 ;;
        --min-instances) MIN_INSTANCES="$2"; shift 2 ;;
        --memory)        MEMORY="$2"; shift 2 ;;
        --cpu)           CPU="$2"; shift 2 ;;
        --poll-schedule) POLL_SCHEDULE="$2"; shift 2 ;;
        --foundry-url)   FOUNDRY_URL="$2"; shift 2 ;;
        --skip-scheduler) SKIP_SCHEDULER=true; shift ;;
        --dry-run)       DRY_RUN=true; shift ;;
        *)               echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$AGENT_NAME" ]]; then
    echo "Usage: $0 <agent-name> [options]"
    echo ""
    echo "Example:"
    echo "  $0 my-agent --foundry-url http://foundry.example.com"
    echo "  $0 my-agent --agent-space /path/to/agent_space --min-instances 1"
    exit 1
fi

# Sanitize agent name for Cloud Run (lowercase, hyphens, max 63 chars)
SERVICE_NAME="$(echo "$AGENT_NAME" | tr '[:upper:]' '[:lower:]' | tr '_' '-' | head -c 63)"

if [[ -z "$GCP_PROJECT" ]] && command -v gcloud &>/dev/null; then
    GCP_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
    if [[ "$GCP_PROJECT" == "(unset)" ]]; then
        GCP_PROJECT=""
    fi
fi

if [[ -z "$GCP_PROJECT" ]]; then
    echo "Error: GCP project is required. Pass --project <id> or run: gcloud config set project <id>"
    exit 1
fi

IMAGE_TAG="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/${SERVICE_NAME}:latest"
AR_HOST="${GCP_REGION}-docker.pkg.dev"
SCHEDULER_JOB="poll-${SERVICE_NAME}"

log() { echo "[deploy] $*"; }
elapsed_seconds() {
    local start="$1"
    local end
    end="$(date +%s)"
    echo "$((end - start))s"
}
metadata_project_number() {
    if command -v curl &>/dev/null; then
        curl -fsS \
            --connect-timeout 1 \
            --max-time 2 \
            -H "Metadata-Flavor: Google" \
            "http://metadata.google.internal/computeMetadata/v1/project/numeric-project-id" 2>/dev/null || true
    fi
}
run() {
    if $DRY_RUN; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# ── Pre-flight checks ────────────────────────────────────────────────────────
log "Agent:   $AGENT_NAME"
log "Service: $SERVICE_NAME"
log "Project: $GCP_PROJECT"
log "Region:  $GCP_REGION"
log "Image:   $IMAGE_TAG"

if ! command -v gcloud &>/dev/null; then
    echo "Error: gcloud CLI is required. Install it from https://cloud.google.com/sdk"
    exit 1
fi

log "Ensuring Artifact Registry repository exists..."
STEP_START="$(date +%s)"
if $DRY_RUN; then
    echo "[dry-run] gcloud artifacts repositories describe $AR_REPO --project=$GCP_PROJECT --location=$GCP_REGION"
    echo "[dry-run] gcloud artifacts repositories create $AR_REPO --repository-format=docker --location=$GCP_REGION --project=$GCP_PROJECT --description=CCFoundry agent Cloud Run images --quiet"
else
    if REPO_DESCRIBE_OUTPUT="$(
        gcloud artifacts repositories describe "$AR_REPO" \
            --project="$GCP_PROJECT" \
            --location="$GCP_REGION" \
            --format="value(name)" 2>&1
    )"; then
        log "Artifact Registry repository ready: $AR_REPO"
    elif echo "$REPO_DESCRIBE_OUTPUT" | grep -Eiq "not found|not_found|requested entity was not found"; then
        log "Creating Artifact Registry repository: $AR_REPO"
        gcloud artifacts repositories create "$AR_REPO" \
            --repository-format=docker \
            --location="$GCP_REGION" \
            --project="$GCP_PROJECT" \
            --description="CCFoundry agent Cloud Run images" \
            --quiet
    else
        echo "$REPO_DESCRIBE_OUTPUT"
        exit 1
    fi
fi
log "Artifact Registry check completed in $(elapsed_seconds "$STEP_START")"

# ── Step 1: Prepare build context ─────────────────────────────────────────────
BUILD_DIR=$(mktemp -d "${REPO_ROOT}/.cloudrun-build-XXXXXX")
trap 'rm -rf "$BUILD_DIR"' EXIT

log "Preparing build context in $BUILD_DIR"
STEP_START="$(date +%s)"

# Copy essential dirs
cp -r "$REPO_ROOT/packages" "$BUILD_DIR/packages"
cp -r "$REPO_ROOT/examples" "$BUILD_DIR/examples"
cp "$REPO_ROOT/Dockerfile.cloudrun" "$BUILD_DIR/Dockerfile"

# If custom agent_space is provided, overlay it
if [[ -n "$AGENT_SPACE" && -d "$AGENT_SPACE" ]]; then
    log "Using custom agent_space from $AGENT_SPACE"
    rm -rf "$BUILD_DIR/examples/me_agent/agent_space"
    cp -r "$AGENT_SPACE" "$BUILD_DIR/examples/me_agent/agent_space"
fi

# Write a .dockerignore to keep the build context small
cat > "$BUILD_DIR/.dockerignore" <<'EOF'
**/__pycache__
**/*.pyc
**/*.pyo
**/.git
**/.venv
**/node_modules
**/dist
**/.dev-board
**/*.egg-info
**/build
# Sensitive local agent state — must not be baked into images
**/.env
**/.env.*
**/.foundry_bootstrap.json
**/.foundry_bootstrap.*.json
**/runtime/
**/*.jsonl
**/*.pem
**/*.key
**/*credentials*.json
**/*client_secret*.json
**/*service-account*.json
EOF
log "Build context prepared in $(elapsed_seconds "$STEP_START")"

# ── Step 2: Build & push Docker image ─────────────────────────────────────
log "Configuring Docker for Artifact Registry..."
STEP_START="$(date +%s)"
if $DRY_RUN; then
    echo "[dry-run] gcloud auth configure-docker $AR_HOST --quiet"
elif [[ -f "${HOME}/.docker/config.json" ]] && grep -q "\"${AR_HOST}\"" "${HOME}/.docker/config.json"; then
    log "Docker credential helper already configured for $AR_HOST"
else
    gcloud auth configure-docker "$AR_HOST" --quiet
fi
log "Docker auth configuration completed in $(elapsed_seconds "$STEP_START")"

log "Building Docker image locally with BuildKit cache..."
STEP_START="$(date +%s)"
run env DOCKER_BUILDKIT=1 docker build --progress=plain -f "$BUILD_DIR/Dockerfile" -t "$IMAGE_TAG" "$BUILD_DIR"
log "Docker build completed in $(elapsed_seconds "$STEP_START")"

log "Pushing image to Artifact Registry..."
STEP_START="$(date +%s)"
run docker push "$IMAGE_TAG"
log "Docker push completed in $(elapsed_seconds "$STEP_START")"

# ── Step 3: Deploy to Cloud Run ───────────────────────────────────────────────
log "Deploying to Cloud Run..."
STEP_START="$(date +%s)"

DEPLOY_ARGS=(
    --project="$GCP_PROJECT"
    --region="$GCP_REGION"
    --image="$IMAGE_TAG"
    --platform=managed
    --port=8080
    --memory="$MEMORY"
    --cpu="$CPU"
    --min-instances="$MIN_INSTANCES"
    --max-instances=5
    --timeout=300
    --set-env-vars="AGENT_DEPLOY_MODE=cloud_run"
    --set-env-vars="ME_AGENT_NAME=${AGENT_NAME}"
    --set-env-vars="ME_AGENT_BASE_DIR=/app"
    --set-env-vars="FOUNDRY_DISCOVERY_ENABLE=true"
    --set-env-vars="FOUNDRY_RUNTIME_TRANSPORT=pull"
    --set-env-vars="FOUNDRY_BOOTSTRAP_DELIVERY=poll"
)

EXTRA_ENV_VARS=()
if [[ -n "${FOUNDRY_DISCOVERY_CLAIM_TOKEN:-}" ]]; then
    EXTRA_ENV_VARS+=("FOUNDRY_DISCOVERY_CLAIM_TOKEN=${FOUNDRY_DISCOVERY_CLAIM_TOKEN}")
fi
if [[ -n "${FOUNDRY_DISCOVERY_NONCE:-}" ]]; then
    EXTRA_ENV_VARS+=("FOUNDRY_DISCOVERY_NONCE=${FOUNDRY_DISCOVERY_NONCE}")
fi
if [[ -n "${FOUNDRY_DEVELOPER_IDENTITY_JSON:-}" ]]; then
    EXTRA_ENV_VARS+=("FOUNDRY_DEVELOPER_IDENTITY_JSON=${FOUNDRY_DEVELOPER_IDENTITY_JSON}")
fi
for ENV_NAME in \
    FOUNDRY_AGENT_SOURCE_ID \
    FOUNDRY_REGISTERED_AGENT_NAME \
    FOUNDRY_REGISTRATION_STATUS \
    FOUNDRY_APPROVED_AT \
    FOUNDRY_ALLOCATED_RESOURCES_JSON \
    AGENT_SECRET \
    LLM_MODEL \
    LLM_API_KEY \
    LLM_API_BASE \
    LLM_ALLOWED_MODELS_JSON \
    OPENAI_API_KEY \
    OPENAI_BASE_URL \
    FOUNDRY_ALLOWED_MODELS_JSON
do
    if [[ -n "${!ENV_NAME:-}" ]]; then
        EXTRA_ENV_VARS+=("${ENV_NAME}=${!ENV_NAME}")
    fi
done
if [[ ${#EXTRA_ENV_VARS[@]} -gt 0 ]]; then
    EXTRA_ENV_JOINED="$(IFS=@; echo "${EXTRA_ENV_VARS[*]}")"
    DEPLOY_ARGS+=(--set-env-vars="^@^${EXTRA_ENV_JOINED}")
fi

if [[ -n "$FOUNDRY_URL" ]]; then
    DEPLOY_ARGS+=(--set-env-vars="FOUNDRY_BASE_URL=${FOUNDRY_URL}")
fi

run gcloud run deploy "$SERVICE_NAME" "${DEPLOY_ARGS[@]}" --quiet
log "Cloud Run deploy completed in $(elapsed_seconds "$STEP_START")"

# ── Step 4: Get the service URL ───────────────────────────────────────────────
if ! $DRY_RUN; then
    SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
        --project="$GCP_PROJECT" \
        --region="$GCP_REGION" \
        --format="value(status.url)" 2>/dev/null)
    log "Service URL: $SERVICE_URL"
    log "Runtime will infer FOUNDRY_AGENT_PUBLIC_URL from the first Cloud Scheduler poll."
else
    SERVICE_URL="https://${SERVICE_NAME}-xxx.run.app"
    log "(dry-run) Service URL would be: $SERVICE_URL"
fi

# ── Step 5: Create Cloud Scheduler job ────────────────────────────────────────
if ! $SKIP_SCHEDULER; then
    log "Creating Cloud Scheduler job: $SCHEDULER_JOB"
    STEP_START="$(date +%s)"

    # Use the default Compute Engine service account for OIDC auth. It exists
    # on GCE-backed projects without requiring an App Engine appspot account.
    if $DRY_RUN; then
        PROJECT_NUMBER="<project-number>"
    else
        PROJECT_NUMBER="$(metadata_project_number)"
        if [[ -z "$PROJECT_NUMBER" ]]; then
            PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')"
        fi
    fi
    SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

    if $DRY_RUN; then
        echo "[dry-run] gcloud run services get-iam-policy $SERVICE_NAME --project=$GCP_PROJECT --region=$GCP_REGION --flatten=bindings[].members --filter=bindings.role=roles/run.invoker AND bindings.members=serviceAccount:${SA_EMAIL} --format=value(bindings.members)"
        echo "[dry-run] gcloud run services add-iam-policy-binding $SERVICE_NAME --project=$GCP_PROJECT --region=$GCP_REGION --member=serviceAccount:${SA_EMAIL} --role=roles/run.invoker --quiet"
    elif INVOKER_MEMBER="$(
        gcloud run services get-iam-policy "$SERVICE_NAME" \
            --project="$GCP_PROJECT" \
            --region="$GCP_REGION" \
            --flatten="bindings[].members" \
            --filter="bindings.role=roles/run.invoker AND bindings.members=serviceAccount:${SA_EMAIL}" \
            --format="value(bindings.members)" 2>/dev/null
    )" && [[ -n "$INVOKER_MEMBER" ]]; then
        log "Scheduler service account already has Cloud Run invoker access"
    else
        timeout 90s gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
            --project="$GCP_PROJECT" \
            --region="$GCP_REGION" \
            --member="serviceAccount:${SA_EMAIL}" \
            --role=roles/run.invoker \
            --quiet
    fi

    SCHEDULER_ARGS=(
        --project="$GCP_PROJECT"
        --location="$GCP_REGION"
        --schedule="$POLL_SCHEDULE"
        --uri="${SERVICE_URL}/foundry/poll"
        --http-method=POST
        --oidc-service-account-email="$SA_EMAIL"
        --oidc-token-audience="$SERVICE_URL"
        --attempt-deadline=120s
        --quiet
    )
    if $DRY_RUN; then
        echo "[dry-run] gcloud scheduler jobs describe $SCHEDULER_JOB --project=$GCP_PROJECT --location=$GCP_REGION"
        echo "[dry-run] gcloud scheduler jobs update http $SCHEDULER_JOB ${SCHEDULER_ARGS[*]}"
        echo "[dry-run] gcloud scheduler jobs create http $SCHEDULER_JOB ${SCHEDULER_ARGS[*]}"
    elif gcloud scheduler jobs describe "$SCHEDULER_JOB" \
        --project="$GCP_PROJECT" \
        --location="$GCP_REGION" >/dev/null 2>&1; then
        timeout 90s gcloud scheduler jobs update http "$SCHEDULER_JOB" "${SCHEDULER_ARGS[@]}"
        log "Scheduler job updated: schedule ${POLL_SCHEDULE}"
    else
        timeout 90s gcloud scheduler jobs create http "$SCHEDULER_JOB" "${SCHEDULER_ARGS[@]}"
        log "Scheduler job created: schedule ${POLL_SCHEDULE}"
    fi
    log "Scheduler setup completed in $(elapsed_seconds "$STEP_START")"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✅ Agent deployed to Cloud Run!"
echo ""
echo "  Service:    $SERVICE_NAME"
echo "  URL:        $SERVICE_URL"
echo "  Health:     ${SERVICE_URL}/health"
echo "  Agent Card: ${SERVICE_URL}/.well-known/agent-card.json"
echo "  Poll:       ${SERVICE_URL}/foundry/poll"
if ! $SKIP_SCHEDULER; then
echo "  Scheduler:  $SCHEDULER_JOB (${POLL_SCHEDULE})"
fi
echo ""
echo "  Test it:"
echo "    curl ${SERVICE_URL}/health"
echo "    curl -X POST ${SERVICE_URL}/foundry/poll"
echo "════════════════════════════════════════════════════════════════"
