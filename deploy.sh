#!/usr/bin/env bash
set -euo pipefail

IMAGE_PROJECT_DEFAULT="glassy-fort-497911-u3"
IMAGE_FAMILY_DEFAULT="ccfoundry-agent-kit"
INSTANCE_NAME_DEFAULT="ccfoundry-dev-board"
ZONE_DEFAULT="us-central1-a"
MACHINE_TYPE_DEFAULT="e2-standard-4"
BOOT_DISK_SIZE_DEFAULT="30GB"
NETWORK_DEFAULT="default"
NETWORK_TAG_DEFAULT="ccfoundry-dev-board"
FIREWALL_RULE_DEFAULT="allow-ccfoundry-dev-board"
SERVICE_ACCOUNT_ID_DEFAULT="ccfoundry-dev-board"

PROJECT_ID="${CCFOUNDRY_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
CREATE_PROJECT_ID="${CCFOUNDRY_CREATE_PROJECT:-}"
IMAGE_PROJECT="${CCFOUNDRY_IMAGE_PROJECT:-$IMAGE_PROJECT_DEFAULT}"
IMAGE_FAMILY="${CCFOUNDRY_IMAGE_FAMILY:-$IMAGE_FAMILY_DEFAULT}"
INSTANCE_NAME="${CCFOUNDRY_INSTANCE_NAME:-$INSTANCE_NAME_DEFAULT}"
ZONE="${CCFOUNDRY_ZONE:-$ZONE_DEFAULT}"
MACHINE_TYPE="${CCFOUNDRY_MACHINE_TYPE:-$MACHINE_TYPE_DEFAULT}"
BOOT_DISK_SIZE="${CCFOUNDRY_BOOT_DISK_SIZE:-$BOOT_DISK_SIZE_DEFAULT}"
NETWORK="${CCFOUNDRY_NETWORK:-$NETWORK_DEFAULT}"
NETWORK_TAG="${CCFOUNDRY_NETWORK_TAG:-$NETWORK_TAG_DEFAULT}"
FIREWALL_RULE="${CCFOUNDRY_FIREWALL_RULE:-$FIREWALL_RULE_DEFAULT}"
SOURCE_RANGES="${CCFOUNDRY_SOURCE_RANGES:-}"
SERVICE_ACCOUNT_ID="${CCFOUNDRY_SERVICE_ACCOUNT_ID:-$SERVICE_ACCOUNT_ID_DEFAULT}"
SETUP_SERVICE_ACCOUNT="${CCFOUNDRY_SETUP_SERVICE_ACCOUNT:-true}"
DRY_RUN=false
SERVICE_ACCOUNT_EMAIL=""

usage() {
    cat <<'EOF'
Deploy the CCFoundry Agent Dev Board to Google Compute Engine.

Usage:
  ./deploy.sh [options]

Options:
  --project <id>             Google Cloud project ID
  --create-project <id>      Create and use a new Google Cloud project
  --zone <zone>              Compute Engine zone (default: us-central1-a)
  --instance-name <name>     VM instance name (default: ccfoundry-dev-board)
  --machine-type <type>      VM machine type (default: e2-standard-4)
  --boot-disk-size <size>    Boot disk size (default: 30GB)
  --no-service-account-setup Skip dedicated service account and IAM setup
  --dry-run                  Print actions without creating resources
  -h, --help                 Show this help

Environment variables with the CCFOUNDRY_ prefix can also override defaults,
for example CCFOUNDRY_ZONE=asia-east2-a or CCFOUNDRY_INSTANCE_NAME=my-board.
Dev Board has no login, so its ports are opened only to your public IP by default.
Use CCFOUNDRY_SOURCE_RANGES=<cidr>[,<cidr>] to choose who can reach it; in Cloud Shell
the script asks for the IP of the browser you will use. 0.0.0.0/0 opens it to everyone.
Use CCFOUNDRY_CREATE_PROJECT=<id> to create a new project before deployment.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)
            PROJECT_ID="$2"
            shift 2
            ;;
        --create-project)
            CREATE_PROJECT_ID="$2"
            shift 2
            ;;
        --zone)
            ZONE="$2"
            shift 2
            ;;
        --instance-name)
            INSTANCE_NAME="$2"
            shift 2
            ;;
        --machine-type)
            MACHINE_TYPE="$2"
            shift 2
            ;;
        --boot-disk-size)
            BOOT_DISK_SIZE="$2"
            shift 2
            ;;
        --no-service-account-setup)
            SETUP_SERVICE_ACCOUNT=false
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

log() {
    echo "[ccfoundry] $*"
}

warn() {
    echo "[ccfoundry] Warning: $*" >&2
}

fail() {
    echo "[ccfoundry] Error: $*" >&2
    exit 1
}

run() {
    if $DRY_RUN; then
        printf '[dry-run]'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

valid_ipv4() {
    local ip="$1"
    local octet
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    local IFS=.
    for octet in $ip; do
        ((10#$octet <= 255)) || return 1
    done
}

valid_source_ranges() {
    local ranges="$1"
    local item address prefix
    [[ -n "$ranges" ]] || return 1
    local IFS=,
    for item in $ranges; do
        [[ "$item" =~ ^([0-9.]+)/([0-9]{1,2})$ ]] || return 1
        address="${BASH_REMATCH[1]}"
        prefix="${BASH_REMATCH[2]}"
        valid_ipv4 "$address" || return 1
        ((10#$prefix <= 32)) || return 1
    done
}

warn_open_to_internet() {
    warn "=============================================================================="
    warn "CCFOUNDRY_SOURCE_RANGES includes 0.0.0.0/0: Dev Board ports 3000 and 8090 will be"
    warn "reachable from the whole internet. The Dev Board API has no login and can start"
    warn "local processes and deploy with this VM's Google Cloud credentials."
    warn "=============================================================================="
}

detect_public_ip() {
    local endpoint ip
    command -v curl >/dev/null 2>&1 || return 1
    for endpoint in ${CCFOUNDRY_PUBLIC_IP_ENDPOINTS:-https://checkip.amazonaws.com https://api.ipify.org}; do
        ip="$(curl -fsS --max-time 5 "$endpoint" 2>/dev/null | tr -d '[:space:]')" || continue
        if valid_ipv4 "$ip"; then
            printf '%s\n' "$ip"
            return 0
        fi
    done
    return 1
}

resolve_source_ranges() {
    local answer ip

    if [[ -n "$SOURCE_RANGES" ]]; then
        SOURCE_RANGES="${SOURCE_RANGES// /}"
        valid_source_ranges "$SOURCE_RANGES" ||
            fail "CCFOUNDRY_SOURCE_RANGES must be comma-separated IPv4 CIDRs, for example 203.0.113.7/32."
        if [[ ",$SOURCE_RANGES," == *",0.0.0.0/0,"* ]]; then
            warn_open_to_internet
        fi
        return 0
    fi

    if [[ "${CLOUD_SHELL:-}" == "true" ]]; then
        # Cloud Shell egresses from a Google address, not from the browser that opens Dev Board.
        if [[ -t 0 && -t 1 ]]; then
            echo ""
            echo "Dev Board has no login, so its ports are opened only to the address you choose."
            echo "Open https://checkip.amazonaws.com in the browser you will use for Dev Board and paste the address shown."
            while [[ -z "$SOURCE_RANGES" ]]; do
                read -r -p "Allowed IP or CIDR (type 'any' to allow the whole internet): " answer
                answer="${answer// /}"
                if [[ "$answer" == "any" ]]; then
                    SOURCE_RANGES="0.0.0.0/0"
                    warn_open_to_internet
                elif valid_ipv4 "$answer"; then
                    SOURCE_RANGES="${answer}/32"
                elif valid_source_ranges "$answer"; then
                    SOURCE_RANGES="$answer"
                else
                    warn "Enter an IPv4 address such as 203.0.113.7, a CIDR such as 203.0.113.0/24, or 'any'."
                fi
            done
            return 0
        fi
        if $DRY_RUN; then
            SOURCE_RANGES="<your-browser-ip>/32"
            warn "Dry run: a real run in Cloud Shell asks for your browser's public IP or needs CCFOUNDRY_SOURCE_RANGES."
            return 0
        fi
        fail "Cloud Shell cannot detect the IP of your browser. Run: CCFOUNDRY_SOURCE_RANGES=<your-ip>/32 ./deploy.sh (see https://checkip.amazonaws.com)."
    fi

    if ip="$(detect_public_ip)"; then
        SOURCE_RANGES="${ip}/32"
        log "Dev Board ports will be open only to your public IP ${ip} (set CCFOUNDRY_SOURCE_RANGES to change)."
        return 0
    fi
    if $DRY_RUN; then
        SOURCE_RANGES="<your-public-ip>/32"
        warn "Dry run: could not detect your public IP; a real run needs CCFOUNDRY_SOURCE_RANGES."
        return 0
    fi
    fail "Could not detect your public IP. Set CCFOUNDRY_SOURCE_RANGES=<your-ip>/32 (0.0.0.0/0 allows everyone and is not recommended)."
}

gcloud_value() {
    gcloud "$@" 2>/dev/null || true
}

print_cyan() {
    if [[ -t 1 ]]; then
        printf '\033[1;36m%s\033[0m\n' "$*"
    else
        printf '%s\n' "$*"
    fi
}

require_gcloud() {
    if ! command -v gcloud >/dev/null 2>&1; then
        fail "gcloud CLI is required. Run this from Google Cloud Shell or install the Google Cloud SDK."
    fi
}

valid_project_id() {
    [[ "$1" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]
}

project_billing_console_url() {
    printf 'https://console.cloud.google.com/billing/linkedaccount?project=%s\n' "$1"
}

create_project() {
    local new_project_id="$1"

    if ! valid_project_id "$new_project_id"; then
        warn "Project IDs must be 6-30 chars, start with a lowercase letter, and contain only lowercase letters, digits, and hyphens."
        return 1
    fi

    log "Creating Google Cloud project $new_project_id..."
    if $DRY_RUN; then
        run gcloud projects create "$new_project_id" \
            --name="CCFoundry Dev Board" \
            --set-as-default \
            --quiet
    else
        gcloud projects create "$new_project_id" \
            --name="CCFoundry Dev Board" \
            --set-as-default \
            --quiet >/dev/null || return 1
    fi

    PROJECT_ID="$new_project_id"
    log "Created project: $PROJECT_ID"
}

create_project_interactively() {
    local new_project_id

    echo ""
    echo "Create a new Google Cloud project."
    echo "The project ID must be globally unique, for example: ccfoundry-demo-$(date +%Y%m%d)"
    read -r -p "New project ID (blank to cancel): " new_project_id

    if [[ -z "$new_project_id" ]]; then
        return 1
    fi

    create_project "$new_project_id"
}

select_billing_account() {
    local account
    local account_id
    local choice
    local display_name
    local index
    local billing_accounts=()

    mapfile -t billing_accounts < <(gcloud billing accounts list \
        --filter='open=true' \
        --format='value(name,displayName)' 2>/dev/null || true)

    if ((${#billing_accounts[@]} == 0)); then
        return 1
    fi

    if ((${#billing_accounts[@]} == 1)); then
        account="${billing_accounts[0]}"
        BILLING_ACCOUNT_ID="${account%%$'\t'*}"
        BILLING_ACCOUNT_ID="${BILLING_ACCOUNT_ID#billingAccounts/}"
        return 0
    fi

    echo ""
    echo "Available billing accounts:"
    for index in "${!billing_accounts[@]}"; do
        account="${billing_accounts[$index]}"
        account_id="${account%%$'\t'*}"
        display_name="${account#*$'\t'}"
        printf "  %2d) %s  %s\n" "$((index + 1))" "${account_id#billingAccounts/}" "$display_name"
    done
    echo ""

    while [[ -z "${BILLING_ACCOUNT_ID:-}" ]]; do
        read -r -p "Billing account number (blank to skip): " choice
        if [[ -z "$choice" ]]; then
            return 1
        fi
        if [[ "$choice" =~ ^[0-9]+$ ]] && ((choice >= 1 && choice <= ${#billing_accounts[@]})); then
            account="${billing_accounts[$((choice - 1))]}"
            BILLING_ACCOUNT_ID="${account%%$'\t'*}"
            BILLING_ACCOUNT_ID="${BILLING_ACCOUNT_ID#billingAccounts/}"
        else
            warn "Please enter one of the listed billing account numbers."
        fi
    done
}

ensure_project_billing() {
    local billing_enabled

    if $DRY_RUN; then
        run gcloud billing projects describe "$PROJECT_ID"
        return 0
    fi

    billing_enabled="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || true)"
    if [[ "$billing_enabled" == "True" ]]; then
        return 0
    fi

    if [[ -z "$billing_enabled" ]]; then
        warn "Could not confirm billing for project $PROJECT_ID. Continuing; VM creation will fail if billing is not enabled."
        return 0
    fi

    warn "Billing is not enabled for project $PROJECT_ID."
    BILLING_ACCOUNT_ID=""
    if [[ -t 0 && -t 1 ]] && select_billing_account; then
        log "Linking billing account $BILLING_ACCOUNT_ID to project $PROJECT_ID..."
        if gcloud billing projects link "$PROJECT_ID" \
            --billing-account="$BILLING_ACCOUNT_ID" \
            --quiet >/dev/null; then
            return 0
        fi
        warn "Could not link billing account automatically."
    fi

    fail "Billing is required to create the VM. Open $(project_billing_console_url "$PROJECT_ID"), enable billing, then run ./deploy.sh again."
}

resolve_project() {
    if [[ -n "$CREATE_PROJECT_ID" ]]; then
        create_project "$CREATE_PROJECT_ID" || fail "Could not create project $CREATE_PROJECT_ID. Try another globally unique project ID or create one in the Cloud Console."
    fi

    if [[ -z "$PROJECT_ID" ]]; then
        PROJECT_ID="$(gcloud_value config get-value project)"
        if [[ "$PROJECT_ID" == "(unset)" ]]; then
            PROJECT_ID=""
        fi
    fi
    if [[ -z "$PROJECT_ID" ]]; then
        prompt_for_project || true
    fi
    if [[ -z "$PROJECT_ID" ]]; then
        fail "No Google Cloud project selected. Use the tutorial project picker or run: gcloud config set project <PROJECT_ID>"
    fi
    if ! $DRY_RUN; then
        gcloud config set project "$PROJECT_ID" >/dev/null 2>&1 || warn "Could not set gcloud's default project, but continuing with --project=$PROJECT_ID."
    fi
}

prompt_for_project() {
    local choice
    local index
    local projects=()

    if [[ ! -t 0 || ! -t 1 ]]; then
        return 1
    fi

    log "No Google Cloud project is selected."
    log "Fetching projects visible to your account..."
    mapfile -t projects < <(gcloud projects list --format='value(projectId)' --sort-by=projectId --limit=50 2>/dev/null || true)

    if ((${#projects[@]} > 0)); then
        echo ""
        echo "Available projects:"
        for index in "${!projects[@]}"; do
            printf "  %2d) %s\n" "$((index + 1))" "${projects[$index]}"
        done
        echo ""
        echo "Enter a project number, paste a project ID, type 'new' to create one, or press Ctrl+C to stop."
    else
        echo ""
        echo "No projects were returned by gcloud projects list."
        echo "Paste the project ID you want to use, type 'new' to create one, or press Ctrl+C to stop."
    fi

    while [[ -z "$PROJECT_ID" ]]; do
        read -r -p "Project: " choice
        if [[ -z "$choice" ]]; then
            continue
        fi
        if [[ "$choice" == "new" || "$choice" == "create" ]]; then
            create_project_interactively || true
        elif [[ "$choice" =~ ^[0-9]+$ ]] && ((choice >= 1 && choice <= ${#projects[@]})); then
            PROJECT_ID="${projects[$((choice - 1))]}"
        elif valid_project_id "$choice"; then
            PROJECT_ID="$choice"
        else
            warn "Please enter a project number, a valid project ID, or 'new'."
        fi
    done

    log "Selected project: $PROJECT_ID"
}

enable_required_apis() {
    log "Enabling required Google Cloud APIs in project $PROJECT_ID..."
    run gcloud services enable \
        compute.googleapis.com \
        iam.googleapis.com \
        run.googleapis.com \
        artifactregistry.googleapis.com \
        cloudscheduler.googleapis.com \
        --project="$PROJECT_ID" \
        --quiet
}

ensure_firewall_rule() {
    log "Configuring firewall rule $FIREWALL_RULE for tcp:3000 and tcp:8090..."
    if $DRY_RUN; then
        run gcloud compute firewall-rules describe "$FIREWALL_RULE" --project="$PROJECT_ID"
        run gcloud compute firewall-rules create "$FIREWALL_RULE" \
            --project="$PROJECT_ID" \
            --network="$NETWORK" \
            --allow=tcp:3000,tcp:8090 \
            --source-ranges="$SOURCE_RANGES" \
            --target-tags="$NETWORK_TAG" \
            --quiet
        return
    fi

    if gcloud compute firewall-rules describe "$FIREWALL_RULE" --project="$PROJECT_ID" >/dev/null 2>&1; then
        gcloud compute firewall-rules update "$FIREWALL_RULE" \
            --project="$PROJECT_ID" \
            --allow=tcp:3000,tcp:8090 \
            --source-ranges="$SOURCE_RANGES" \
            --target-tags="$NETWORK_TAG" \
            --quiet >/dev/null
    else
        gcloud compute firewall-rules create "$FIREWALL_RULE" \
            --project="$PROJECT_ID" \
            --network="$NETWORK" \
            --allow=tcp:3000,tcp:8090 \
            --source-ranges="$SOURCE_RANGES" \
            --target-tags="$NETWORK_TAG" \
            --quiet >/dev/null
    fi
}

grant_project_role() {
    local member="$1"
    local role="$2"
    log "Granting $role to $member..."
    if $DRY_RUN; then
        run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="$member" \
            --role="$role" \
            --quiet
    else
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="$member" \
            --role="$role" \
            --quiet >/dev/null || return 1
    fi
}

grant_service_account_user() {
    local target_sa="$1"
    local member="$2"

    if $DRY_RUN; then
        run gcloud iam service-accounts add-iam-policy-binding "$target_sa" \
            --project="$PROJECT_ID" \
            --member="$member" \
            --role=roles/iam.serviceAccountUser \
            --quiet
        return 0
    fi

    if gcloud iam service-accounts describe "$target_sa" --project="$PROJECT_ID" >/dev/null 2>&1; then
        gcloud iam service-accounts add-iam-policy-binding "$target_sa" \
            --project="$PROJECT_ID" \
            --member="$member" \
            --role=roles/iam.serviceAccountUser \
            --quiet >/dev/null || return 1
    else
        warn "Service account $target_sa was not found; skipping actAs binding."
    fi
}

setup_dev_board_service_account() {
    local project_number
    local service_account_email
    local default_compute_sa
    local member

    if [[ "$SETUP_SERVICE_ACCOUNT" != "true" ]]; then
        return 1
    fi

    if $DRY_RUN; then
        project_number="<project-number>"
    else
        project_number="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')" || return 1
        if [[ -z "$project_number" ]]; then
            return 1
        fi
    fi

    service_account_email="${SERVICE_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
    default_compute_sa="${project_number}-compute@developer.gserviceaccount.com"
    member="serviceAccount:${service_account_email}"

    log "Preparing service account $service_account_email for Dev Board Cloud Run deploys..."
    if $DRY_RUN; then
        run gcloud iam service-accounts describe "$service_account_email" --project="$PROJECT_ID"
        run gcloud iam service-accounts create "$SERVICE_ACCOUNT_ID" \
            --project="$PROJECT_ID" \
            --display-name="CCFoundry Dev Board" \
            --quiet
    elif gcloud iam service-accounts describe "$service_account_email" --project="$PROJECT_ID" >/dev/null 2>&1; then
        log "Service account already exists."
    else
        gcloud iam service-accounts create "$SERVICE_ACCOUNT_ID" \
            --project="$PROJECT_ID" \
            --display-name="CCFoundry Dev Board" \
            --quiet >/dev/null || return 1
    fi

    grant_project_role "$member" roles/run.admin || return 1
    grant_project_role "$member" roles/artifactregistry.admin || return 1
    grant_project_role "$member" roles/cloudscheduler.admin || return 1
    grant_project_role "$member" roles/logging.logWriter || return 1
    grant_project_role "$member" roles/monitoring.metricWriter || return 1

    log "Granting service account actAs permissions needed by Cloud Run and Scheduler..."
    grant_service_account_user "$service_account_email" "$member" || return 1
    grant_service_account_user "$default_compute_sa" "$member" || return 1

    SERVICE_ACCOUNT_EMAIL="$service_account_email"
    return 0
}

instance_exists() {
    gcloud compute instances describe "$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" >/dev/null 2>&1
}

instance_status() {
    gcloud compute instances describe "$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" \
        --format='value(status)' 2>/dev/null || true
}

create_or_start_instance() {
    local service_account_email="${1:-}"
    local base_create_args
    local create_args
    base_create_args=(
        "$INSTANCE_NAME"
        --project="$PROJECT_ID"
        --zone="$ZONE"
        --machine-type="$MACHINE_TYPE"
        --boot-disk-size="$BOOT_DISK_SIZE"
        --boot-disk-type=pd-balanced
        --image-family="$IMAGE_FAMILY"
        --image-project="$IMAGE_PROJECT"
        --network="$NETWORK"
        --tags="$NETWORK_TAG"
        --metadata=block-project-ssh-keys=TRUE
        --scopes=https://www.googleapis.com/auth/cloud-platform
        --labels=app=ccfoundry-dev-board
        --quiet
    )
    create_args=("${base_create_args[@]}")

    if [[ -n "$service_account_email" ]]; then
        create_args+=(--service-account="$service_account_email")
    fi

    if $DRY_RUN; then
        run gcloud compute instances create "${create_args[@]}"
        return
    fi

    if instance_exists; then
        local status
        status="$(instance_status)"
        log "Instance $INSTANCE_NAME already exists with status $status."
        if [[ "$status" == "TERMINATED" ]]; then
            log "Starting existing instance..."
            gcloud compute instances start "$INSTANCE_NAME" \
                --project="$PROJECT_ID" \
                --zone="$ZONE" \
                --quiet
        fi
        return
    fi

    log "Creating VM $INSTANCE_NAME from image family $IMAGE_FAMILY..."
    if ! gcloud compute instances create "${create_args[@]}"; then
        if [[ -n "$service_account_email" ]]; then
            warn "Creation with dedicated service account failed. Retrying with the project default Compute Engine service account."
            gcloud compute instances create "${base_create_args[@]}"
        else
            return 1
        fi
    fi
}

external_ip() {
    gcloud compute instances describe "$INSTANCE_NAME" \
        --project="$PROJECT_ID" \
        --zone="$ZONE" \
        --format='get(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || true
}

wait_for_ip() {
    local ip=""
    for _ in $(seq 1 60); do
        ip="$(external_ip)"
        if [[ -n "$ip" ]]; then
            echo "$ip"
            return 0
        fi
        sleep 3
    done
    return 1
}

wait_for_dev_board() {
    local ip="$1"

    if ! command -v curl >/dev/null 2>&1; then
        warn "curl is not available; skipping HTTP readiness check."
        return 0
    fi

    log "Waiting for Dev Board services to become reachable..."
    for _ in $(seq 1 60); do
        if curl -fsS --max-time 3 "http://${ip}:8090/health" >/dev/null 2>&1 &&
           curl -fsSI --max-time 3 "http://${ip}:3000" >/dev/null 2>&1; then
            return 0
        fi
        sleep 5
    done

    warn "The VM was created, but Dev Board was not reachable before the readiness timeout."
    return 0
}

main() {
    local region
    local ip
    local service_account_email=""

    require_gcloud
    resolve_project
    ensure_project_billing
    region="${ZONE%-*}"

    log "Deploying CCFoundry Agent Dev Board"
    log "Project:      $PROJECT_ID"
    log "Zone:         $ZONE"
    log "Region:       $region"
    log "Instance:     $INSTANCE_NAME"
    log "Machine type: $MACHINE_TYPE"
    log "Image:        $IMAGE_PROJECT/$IMAGE_FAMILY"

    resolve_source_ranges
    log "Allowed from: $SOURCE_RANGES"

    enable_required_apis
    ensure_firewall_rule

    if setup_dev_board_service_account; then
        service_account_email="$SERVICE_ACCOUNT_EMAIL"
        log "VM service account: $service_account_email"
    else
        warn "Using the project default Compute Engine service account. Dev Board will still start, but Cloud Run deploys may need additional IAM roles."
    fi

    create_or_start_instance "$service_account_email"

    if $DRY_RUN; then
        log "Dry run complete."
        return 0
    fi

    ip="$(wait_for_ip)" || fail "Could not read the VM external IP address."
    wait_for_dev_board "$ip"

    local web_url="http://${ip}:3000"
    local health_url="http://${ip}:8090/health"

    printf '%s\n' "$web_url" > .ccfoundry-dev-board-url
    printf '%s\n' "$health_url" > .ccfoundry-dev-board-health-url

    echo ""
    echo "CCFoundry Agent Dev Board is ready."
    echo ""
    echo "Click the Web UI link below to open Dev Board:"
    print_cyan "Web UI:     $web_url"
    print_cyan "Health API: $health_url"
    echo ""
    echo "Instance:   $INSTANCE_NAME"
    echo "Zone:       $ZONE"
    echo "Project:    $PROJECT_ID"
    echo "Reachable from: $SOURCE_RANGES (re-run with CCFOUNDRY_SOURCE_RANGES to change)"
    echo ""
    echo "The VM was created with the cloud-platform OAuth scope. If Cloud Run deploys"
    echo "inside Dev Board still fail, check that the VM service account has Cloud Run,"
    echo "Artifact Registry, Cloud Scheduler, and service-account actAs permissions."
    echo ""
    echo "Web UI URL saved to .ccfoundry-dev-board-url"
}

# Only run when executed; sourcing (tests) just loads the functions.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
