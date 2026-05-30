#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy-cloudrun.sh — Deploy a CCFoundry agent to Google Cloud Run
#
# Usage:
#   ./scripts/deploy-cloudrun.sh <agent-name> [options]
#
# Options:
#   --project <id>       GCP project ID  (default: glassy-fort-497911-u3)
#   --region <region>    Cloud Run region (default: us-central1)
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
GCP_PROJECT="glassy-fort-497911-u3"
GCP_REGION="us-central1"
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
IMAGE_TAG="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/${SERVICE_NAME}:latest"
SCHEDULER_JOB="poll-${SERVICE_NAME}"

log() { echo "[deploy] $*"; }
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

# ── Step 1: Prepare build context ─────────────────────────────────────────────
BUILD_DIR=$(mktemp -d "${REPO_ROOT}/.cloudrun-build-XXXXXX")
trap 'rm -rf "$BUILD_DIR"' EXIT

log "Preparing build context in $BUILD_DIR"

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
EOF

# ── Step 2: Build & push Docker image ─────────────────────────────────────
log "Configuring Docker for Artifact Registry..."
run gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet

log "Building Docker image locally..."
run docker build -f "$BUILD_DIR/Dockerfile" -t "$IMAGE_TAG" "$BUILD_DIR"

log "Pushing image to Artifact Registry..."
run docker push "$IMAGE_TAG"

# ── Step 3: Deploy to Cloud Run ───────────────────────────────────────────────
log "Deploying to Cloud Run..."

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

if [[ -n "$FOUNDRY_URL" ]]; then
    DEPLOY_ARGS+=(--set-env-vars="FOUNDRY_BASE_URL=${FOUNDRY_URL}")
fi

run gcloud run deploy "$SERVICE_NAME" "${DEPLOY_ARGS[@]}" --quiet

# ── Step 4: Get the service URL ───────────────────────────────────────────────
if ! $DRY_RUN; then
    SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
        --project="$GCP_PROJECT" \
        --region="$GCP_REGION" \
        --format="value(status.url)" 2>/dev/null)
    log "Service URL: $SERVICE_URL"

    # Update FOUNDRY_AGENT_PUBLIC_URL with the actual Cloud Run URL
    run gcloud run services update "$SERVICE_NAME" \
        --project="$GCP_PROJECT" \
        --region="$GCP_REGION" \
        --set-env-vars="FOUNDRY_AGENT_PUBLIC_URL=${SERVICE_URL}" \
        --quiet
else
    SERVICE_URL="https://${SERVICE_NAME}-xxx.run.app"
    log "(dry-run) Service URL would be: $SERVICE_URL"
fi

# ── Step 5: Create Cloud Scheduler job ────────────────────────────────────────
if ! $SKIP_SCHEDULER; then
    log "Creating Cloud Scheduler job: $SCHEDULER_JOB"

    # Get the default compute service account for OIDC auth
    SA_EMAIL="${GCP_PROJECT}@appspot.gserviceaccount.com"

    # Delete existing job if present (ignore errors)
    run gcloud scheduler jobs delete "$SCHEDULER_JOB" \
        --project="$GCP_PROJECT" \
        --location="$GCP_REGION" \
        --quiet 2>/dev/null || true

    run gcloud scheduler jobs create http "$SCHEDULER_JOB" \
        --project="$GCP_PROJECT" \
        --location="$GCP_REGION" \
        --schedule="$POLL_SCHEDULE" \
        --uri="${SERVICE_URL}/foundry/poll" \
        --http-method=POST \
        --oidc-service-account-email="$SA_EMAIL" \
        --oidc-token-audience="$SERVICE_URL" \
        --attempt-deadline=120s \
        --quiet
    log "Scheduler job created: runs every minute"
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
