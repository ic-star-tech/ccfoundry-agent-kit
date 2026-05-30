# Cloud Run Deployment

Deploy your CCFoundry agent to Google Cloud Run for always-available, serverless operation. Your computer can be turned off — the agent keeps running in the cloud, automatically polling Foundry for bounties and tasks.

## Architecture

```
Cloud Scheduler (every 1 min)
    ↓ POST + OIDC Token
Cloud Run (agent container, scale-to-zero)
    ↓ /foundry/poll
Agent SDK (heartbeat + claim tasks)
    ↓ if tasks available
Foundry API (claim → process → complete)
```

When `AGENT_DEPLOY_MODE=cloud_run`:
- Internal polling loops (`_run_loop`, `_heartbeat_loop`) are **disabled**
- A new `POST /foundry/poll` endpoint handles heartbeat + task claiming in a single request
- Cloud Scheduler calls this endpoint every minute with OIDC authentication
- The container scales to zero when idle (no cost)

## Prerequisites

### GCP APIs

Enable these APIs in your Google Cloud project:

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  --project=YOUR_PROJECT_ID
```

### Artifact Registry Repository

Create a Docker repository to store agent images:

```bash
gcloud artifacts repositories create ccfoundry-agents \
  --repository-format=docker \
  --location=us-central1 \
  --project=YOUR_PROJECT_ID
```

### Docker Authentication

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
```

## Quick Deploy

Use the one-line deploy script:

```bash
./scripts/deploy-cloudrun.sh my-agent \
  --foundry-url https://foundry.cochiper.ai \
  --agent-space /path/to/my/agent_space
```

This will:
1. Build a Docker image locally
2. Push it to Artifact Registry
3. Deploy to Cloud Run with `AGENT_DEPLOY_MODE=cloud_run`
4. Create a Cloud Scheduler job to poll every minute

## Deploy From Agent Dev Board

The Dev Board UI exposes the same deploy path in two places:

- `Guided setup -> Create agent -> Run target -> Google Cloud Run`
- `Agent card -> Cloud Run`

Use it after creating an agent source workspace from a template. The UI will:

1. Check whether `gcloud` and Docker are available on the Dev Board API host
2. Show the active Google account, project, and region
3. Show `gcloud auth login` when no active Google account is detected
4. Let you run a dry-run deployment for the selected agent
5. Start the real deploy script asynchronously and stream job logs into the panel

The UI uses the selected agent instance directory as the `--agent-space` input, so installed Skill Store resources are included in the Cloud Run image. If `gcloud` is not authenticated, run `gcloud auth login` on the machine hosting the Dev Board API, then refresh Cloud Run status. On GCE/GVM, an attached service account also works if it has sufficient Cloud Run, Artifact Registry, and Cloud Scheduler permissions.

### Script Options

```
Usage: ./scripts/deploy-cloudrun.sh <agent-name> [options]

Options:
  --project <id>       GCP project ID  (default: glassy-fort-497911-u3)
  --region <region>    Cloud Run region (default: us-central1)
  --agent-space <path> Path to agent_space directory to deploy
  --min-instances <n>  Minimum instances (default: 0 = scale-to-zero)
  --memory <size>      Memory allocation (default: 512Mi)
  --cpu <n>            CPU allocation   (default: 1)
  --poll-schedule <s>  Cloud Scheduler cron (default: "* * * * *")
  --foundry-url <url>  Foundry base URL
  --skip-scheduler     Don't create a Cloud Scheduler job
  --dry-run            Print commands without executing
```

## Manual Deploy

### Step 1: Build Docker Image

```bash
docker build -f Dockerfile.cloudrun \
  -t us-central1-docker.pkg.dev/YOUR_PROJECT/ccfoundry-agents/my-agent:latest .
```

### Step 2: Push to Artifact Registry

```bash
docker push us-central1-docker.pkg.dev/YOUR_PROJECT/ccfoundry-agents/my-agent:latest
```

### Step 3: Deploy to Cloud Run

```bash
gcloud run deploy my-agent \
  --project=YOUR_PROJECT \
  --region=us-central1 \
  --image=us-central1-docker.pkg.dev/YOUR_PROJECT/ccfoundry-agents/my-agent:latest \
  --port=8080 \
  --memory=512Mi \
  --min-instances=0 \
  --max-instances=5 \
  --timeout=300 \
  --set-env-vars="AGENT_DEPLOY_MODE=cloud_run,ME_AGENT_NAME=my-agent,ME_AGENT_BASE_DIR=/app,FOUNDRY_DISCOVERY_ENABLE=true,FOUNDRY_RUNTIME_TRANSPORT=pull"
```

### Step 4: Create Cloud Scheduler Job

```bash
SERVICE_URL=$(gcloud run services describe my-agent --region=us-central1 --format="value(status.url)")
SA_EMAIL="$(gcloud projects describe YOUR_PROJECT --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

# Grant invoker role to the service account
gcloud run services add-iam-policy-binding my-agent \
  --region=us-central1 \
  --member="serviceAccount:${SA_EMAIL}" \
  --role=roles/run.invoker

# Create the scheduler job
gcloud scheduler jobs create http poll-my-agent \
  --location=us-central1 \
  --schedule="* * * * *" \
  --uri="${SERVICE_URL}/foundry/poll" \
  --http-method=POST \
  --oidc-service-account-email="$SA_EMAIL" \
  --oidc-token-audience="$SERVICE_URL" \
  --attempt-deadline=120s
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_DEPLOY_MODE` | `local` | Set to `cloud_run` to disable internal loops |
| `ME_AGENT_BASE_DIR` | auto | Path to the agent's base directory |
| `ME_AGENT_NAME` | from config | Agent name |
| `FOUNDRY_DISCOVERY_ENABLE` | `false` | Enable Foundry discovery |
| `FOUNDRY_BASE_URL` | — | Foundry server URL |
| `FOUNDRY_RUNTIME_TRANSPORT` | `http_push` | Set to `pull` for Cloud Run |
| `FOUNDRY_AGENT_PUBLIC_URL` | — | Auto-set to the Cloud Run service URL |

## Testing

```bash
# Health check
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://my-agent-xxx.run.app/health

# Manual poll trigger
curl -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://my-agent-xxx.run.app/foundry/poll

# Pause/resume scheduler
gcloud scheduler jobs pause poll-my-agent --location=us-central1
gcloud scheduler jobs resume poll-my-agent --location=us-central1
```

## Cost Estimate

| Component | Free Tier | Beyond Free Tier |
|-----------|-----------|-----------------|
| Cloud Run | 2M requests/month, 360K vCPU-seconds | ~$0.00002400/vCPU-second |
| Cloud Scheduler | 3 jobs free | $0.10/job/month |
| Artifact Registry | 500MB free | $0.10/GB/month |

With scale-to-zero and 1-minute polling, typical cost is **< $5/month**.

## How It Works

The `AGENT_DEPLOY_MODE=cloud_run` environment variable triggers two changes in the SDK:

1. **`FoundryPullRuntime.start()`** skips launching the internal `_run_loop()` background task
2. **`FoundryBootstrap.start()`** skips launching the internal `_heartbeat_loop()` background task

Instead, the new `POST /foundry/poll` endpoint performs both operations in a single HTTP request cycle:
- Calls `heartbeat_once()` — announces to Foundry and processes bootstrap actions
- Calls `poll_once()` up to 30 times — claims and processes pending tasks

This design is fully backward-compatible: without `AGENT_DEPLOY_MODE` set, the agent behaves exactly as before with internal polling loops.
