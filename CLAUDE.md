# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Python ETL pipeline that syncs data between **ClickUp** (project management) and **Clockify** (time tracking), using **Google BigQuery** as an intermediate data store.

## Environment Setup

Credentials are loaded via `python-decouple` from a `.env` file (git-ignored). Required variables:

```
CLICKUP_TOKEN=
CLICKUP_TEAM_ID=
CLOCKIFY_TOKEN=
CLOCKIFY_ATIDIV_WORKSPACE_ID=
```

GCP authentication uses `GOOGLE_APPLICATION_CREDENTIALS` env var pointing to a service account JSON key file. Set this before running locally. When deploying to GCP (Cloud Function / Cloud Run), attach a service account and omit the env var entirely.

## Running locally

```bash
python main.py
```

Optional CLI flags (both override `config.yaml`):
```bash
python main.py --lookback-days 7      # first run: fetch tasks from last 7 days
python main.py --buffer-seconds 3600  # subsequent runs: 1-hour overlap buffer
```

## Architecture

```
settings.py             — loads env vars via decouple; single EnvYAML instance for config.yaml
config.yaml             — non-secret config: dev_mode, rejected_clickup_space_ids, incremental_loading
utils/endpoints.py      — constructs all API URLs from settings
utils/utils.py          — all ClickUp/Clockify API calls (get/create/delete); fetch/sync logic
utils/bigquery_utils.py — gcp2df() and df2gcp() helpers; project=productivity-377410, dataset=tickets_dataset
utils/db.py             — BQ table name constants
main.py                 — ETL orchestrator: spaces → lists → tasks sync
```

**Data flow:** ClickUp API → pandas DataFrame → BigQuery (`df2gcp`) / BigQuery → pandas DataFrame → Clockify API

**Key mapping pattern:** ClickUp Spaces → Clockify Clients; ClickUp Lists → Clockify Projects; ClickUp Tasks → Clockify Tasks. The Clockify `note` field stores the corresponding ClickUp ID to track what's already been synced.

**Task name format on Clockify:** `{clickup_task_id} || {task_name}`

## Incremental loading

- **First run** (BQ table empty): fetches tasks from `now - lookback_days`
- **Subsequent runs**: fetches from `last_pull_date - buffer_seconds` (overlap buffer catches late-arriving tasks)
- Config lives in `config.yaml` under `prod.incremental_loading`; CLI args take priority over config

## Config (`config.yaml`)

| Key | Description |
|-----|-------------|
| `dev_mode` | If `true`, swaps Clockify credentials to the dev workspace under `dev:` and prefixes BQ table names with `_` |
| `prod.rejected_clickup_space_ids` | Space IDs to skip entirely during sync |
| `prod.incremental_loading.lookback_days` | Days to look back on first run (default: 1) |
| `prod.incremental_loading.buffer_seconds` | Overlap buffer on subsequent runs (default: 14400 = 4 hours) |

## Clockify API behaviour

- HTTP 400 with `{"code": 501}` means the resource already exists — logged as WARNING, not error
- Both `create_clockify_client` and `create_clockify_projects` return `None` / `(501, {})` on already-exists; callers handle this gracefully

## Deploying to GCP (Cloud Run Job via Docker)

### Code is already deploy-ready

- `Dockerfile` and `.dockerignore` are included in the repo
- Container runs `python main.py` directly — no HTTP server needed
- Logs stream to stdout; non-zero exit on failure triggers Cloud Run Job retry
- The local credential file (`productivity.json`) is excluded from the image via `.dockerignore`; Cloud Run uses the attached service account instead

### Step 1: Store secrets in Secret Manager

```bash
for SECRET in CLICKUP_TOKEN CLICKUP_TEAM_ID CLOCKIFY_TOKEN CLOCKIFY_ATIDIV_WORKSPACE_ID; do
  echo -n "YOUR_VALUE" | gcloud secrets create $SECRET --data-file=-
done
```

Or via Console: **Secret Manager** → Create secret for each variable, paste the value from your `.env`.

### Step 2: Create a service account

```bash
gcloud iam service-accounts create clickup-sync-sa \
  --display-name "ClickUp Sync Service Account" \
  --project productivity-377410

for ROLE in roles/bigquery.dataEditor roles/bigquery.jobUser roles/secretmanager.secretAccessor roles/run.invoker; do
  gcloud projects add-iam-policy-binding productivity-377410 \
    --member "serviceAccount:clickup-sync-sa@productivity-377410.iam.gserviceaccount.com" \
    --role $ROLE
done
```

### Step 3: Build and push the Docker image

```bash
# Configure Docker to use Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev

# Create Artifact Registry repo (first time only)
gcloud artifacts repositories create clickup-sync \
  --repository-format docker \
  --location us-central1 \
  --project productivity-377410

# Build and push
docker build -t us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest .
docker push us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest
```

### Step 4: Create the Cloud Run Job

```bash
gcloud run jobs create clickup-sync \
  --image us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest \
  --region us-central1 \
  --memory 512Mi \
  --timeout 3600 \
  --service-account clickup-sync-sa@productivity-377410.iam.gserviceaccount.com \
  --set-secrets CLICKUP_TOKEN=CLICKUP_TOKEN:latest,CLICKUP_TEAM_ID=CLICKUP_TEAM_ID:latest,CLOCKIFY_TOKEN=CLOCKIFY_TOKEN:latest,CLOCKIFY_ATIDIV_WORKSPACE_ID=CLOCKIFY_ATIDIV_WORKSPACE_ID:latest \
  --project productivity-377410
```

To update an existing job after pushing a new image:
```bash
gcloud run jobs update clickup-sync \
  --image us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest \
  --region us-central1 --project productivity-377410
```

### Step 5: Schedule it (Cloud Scheduler)

```bash
gcloud scheduler jobs create http clickup-sync-daily \
  --location us-central1 \
  --schedule "0 6 * * *" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/productivity-377410/jobs/clickup-sync:run" \
  --http-method POST \
  --oauth-service-account-email clickup-sync-sa@productivity-377410.iam.gserviceaccount.com \
  --project productivity-377410
```

> Note: Cloud Run Jobs use **OAuth** (not OIDC) for the scheduler auth header.

### Step 6: Test locally with Docker

```bash
docker build -t clickup-sync .
docker run \
  -e CLICKUP_TOKEN=your_token \
  -e CLICKUP_TEAM_ID=your_team_id \
  -e CLOCKIFY_TOKEN=your_token \
  -e CLOCKIFY_ATIDIV_WORKSPACE_ID=your_workspace_id \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json \
  -v "C:/Users/Ashish Agrawal/Documents/Codes/codebase/gcloud/productivity.json:/app/credentials.json:ro" \
  clickup-sync
```

### Run job manually on Cloud Run

```bash
gcloud run jobs execute clickup-sync --region us-central1 --project productivity-377410
```

### View execution logs

```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=clickup-sync" \
  --project productivity-377410 --limit 100 --format "value(textPayload)"
```

### Required IAM roles for the service account

- `BigQuery Data Editor` — read/write BQ tables
- `BigQuery Job User` — run BQ query jobs
- `Secret Manager Secret Accessor` — read secrets at runtime
- `Cloud Run Invoker` — allows Cloud Scheduler to trigger the job
