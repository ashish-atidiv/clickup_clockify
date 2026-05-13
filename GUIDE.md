# Cloud Run Job Deployment Guide

## 1. Overview

This project is a Python ETL pipeline that syncs data between three systems:

- **ClickUp** (project management) — source of spaces, lists, and tasks
- **Google BigQuery** — intermediate store for tracking sync state
- **Clockify** (time tracking) — destination where tasks are created

The script runs on a schedule, fetches new/updated ClickUp tasks since the last run, and creates the corresponding entries in Clockify. It uses incremental loading — on the first run it fetches from the last N days; on subsequent runs it fetches from the last sync timestamp with a small overlap buffer.

It is deployed as a **Cloud Run Job** (not a Cloud Run Service) because it is a batch process that runs to completion on a schedule, with no need for an HTTP endpoint.

---

## 2. Prerequisites

- **Docker Desktop** installed and running locally
- **gcloud CLI** installed — required once for authenticating Docker to Artifact Registry
  - Install: https://cloud.google.com/sdk/docs/install
  - Authenticate: `gcloud auth login`
- **GCP Project:** `productivity-377410`
- **Region:** `us-central1`

---

## 3. Project Structure

```
main.py                 — ETL orchestrator; CLI entry point
Dockerfile              — Container definition (Python 3.10, non-root user)
.dockerignore           — Excludes credentials and caches from the image
requirements.txt        — Python dependencies (pinned)
config.yaml             — Non-secret config: dev_mode, rejected space IDs, incremental loading settings
settings.py             — Loads env vars via python-decouple; reads config.yaml
utils/endpoints.py      — Constructs all ClickUp and Clockify API URLs
utils/utils.py          — All API calls (get/create/delete) and fetch/sync logic
utils/bigquery_utils.py — gcp2df() and df2gcp() helpers for BigQuery I/O
utils/db.py             — BigQuery table name constants
```

### Credentials handling

| Environment | How GCP auth works |
|-------------|-------------------|
| Local (direct) | `GOOGLE_APPLICATION_CREDENTIALS` env var points to `productivity.json` |
| Local (Docker) | Mount `productivity.json` into the container at runtime via `-v` flag |
| Cloud Run Job | Service account attached to the job — no credential file needed |

The four API secrets (`CLICKUP_TOKEN`, `CLICKUP_TEAM_ID`, `CLOCKIFY_TOKEN`, `CLOCKIFY_ATIDIV_WORKSPACE_ID`) are loaded via `python-decouple`, which reads from a `.env` file locally and falls back to environment variables in Cloud Run.

---

## 4. Docker Setup

### Dockerfile

```dockerfile
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
```

Key decisions:
- `PYTHONUNBUFFERED=1` — logs appear in real time (stdout not buffered)
- `PYTHONDONTWRITEBYTECODE=1` — no `.pyc` files written in the container
- Non-root `appuser` — security best practice for containers
- `CMD ["python", "main.py"]` — runs the script directly; no HTTP server needed

### .dockerignore

```
.env
productivity.json
.venv
__pycache__
.claude
*.pyc
```

`.env` and `productivity.json` are excluded so credentials are never baked into the image.

### Testing locally with Docker

```cmd
docker build -t clickup-sync .

docker run -e CLICKUP_TOKEN=your_token -e CLICKUP_TEAM_ID=your_team_id -e CLOCKIFY_TOKEN=your_token -e CLOCKIFY_ATIDIV_WORKSPACE_ID=your_workspace_id -e GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json -v "C:\Users\Ashish Agrawal\Documents\Codes\codebase\gcloud\productivity.json:/app/credentials.json:ro" clickup-sync
```

---

## 5. GCP Setup

### 5.1 Secret Manager

Store the four API credentials as secrets so Cloud Run can inject them as environment variables at runtime.

**Via Console:**
Go to **Secret Manager** → **Create Secret** for each of the following, pasting the value from your `.env` file:
- `CLICKUP_TOKEN`
- `CLICKUP_TEAM_ID`
- `CLOCKIFY_TOKEN`
- `CLOCKIFY_ATIDIV_WORKSPACE_ID`

**Via CLI:**
```bash
for SECRET in CLICKUP_TOKEN CLICKUP_TEAM_ID CLOCKIFY_TOKEN CLOCKIFY_ATIDIV_WORKSPACE_ID; do
  echo -n "YOUR_VALUE" | gcloud secrets create $SECRET --data-file=-
done
```

### 5.2 Service Account

Create a dedicated service account for the Cloud Run Job with the minimum required permissions.

**Via Console:**
1. Go to **IAM & Admin** → **Service Accounts** → **Create Service Account**
2. Name: `clickup-sync-sa`
3. Grant these roles:
   - `BigQuery Data Editor` — read/write BQ tables
   - `BigQuery Job User` — run BQ query jobs
   - `Secret Manager Secret Accessor` — read secrets at runtime
   - `Cloud Run Invoker` — allows Cloud Scheduler to trigger the job

**Via CLI:**
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

### 5.3 Artifact Registry

Create a Docker repository to store the container image.

**Via Console:**
1. Go to **Artifact Registry** → **Repositories** → **Create Repository**
2. Settings:
   - **Name:** `clickup-sync`
   - **Format:** Docker
   - **Location:** `us-central1`
3. Click **Create**

**Required permissions for your user account (`dev@atidiv.com`):**
- `Artifact Registry Administrator` — create/delete repos, push/delete/pull images

Assign via **IAM & Admin** → **IAM** → find your account → add role.

---

## 6. Build & Push Image

### One-time: Authenticate Docker to Artifact Registry

```cmd
gcloud auth configure-docker us-central1-docker.pkg.dev
```

### Build and push

```cmd
docker build -t us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest .
docker push us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest
```

### Re-deploying after code changes

```cmd
docker build -t us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest .
docker push us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest
gcloud run jobs update clickup-sync --image us-central1-docker.pkg.dev/productivity-377410/clickup-sync/app:latest --region us-central1 --project productivity-377410
```

---

## 7. Cloud Run Job

### Create the job

**Via CLI:**
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

**Via Console:**
1. Go to **Cloud Run** → **Jobs** tab → **Create Job**
2. Set container image URL, job name, region, timeout (`3600`), memory (`512 MiB`)
3. Under **Security** → set service account to `clickup-sync-sa`
4. Under **Variables & Secrets** → reference each of the 4 secrets mapped to their env var names
5. Click **Create**

### Run manually

```bash
gcloud run jobs execute clickup-sync --region us-central1 --project productivity-377410
```

Or via Console: **Cloud Run** → **Jobs** → `clickup-sync` → **Execute**.

### View execution logs

**Via Console:**
**Cloud Run** → **Jobs** → `clickup-sync` → **Executions** tab → click any execution → **Logs**

**Via CLI:**
```bash
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=clickup-sync" \
  --project productivity-377410 --limit 100 --format "value(textPayload)"
```

**Via Log Explorer:**
Go to **Logging** → **Log Explorer** and paste:
```
resource.type="cloud_run_job"
resource.labels.job_name="clickup-sync"
```

**Required permissions to view logs:**
- `Logs Viewer` (`roles/logging.viewer`)
- `Cloud Run Viewer` (`roles/run.viewer`)

---

## 8. Scheduling (Cloud Scheduler)

### Create the scheduler

**Via CLI:**
```bash
gcloud scheduler jobs create http clickup-sync-daily \
  --location us-central1 \
  --schedule "0 6 * * *" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/productivity-377410/jobs/clickup-sync:run" \
  --http-method POST \
  --oauth-service-account-email clickup-sync-sa@productivity-377410.iam.gserviceaccount.com \
  --project productivity-377410
```

**Via Console:**
1. Go to **Cloud Scheduler** → **Create Job**
2. Set name, region (`us-central1`), frequency, and timezone
3. Under **Target**:
   - **Target type:** HTTP
   - **URL:** `https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/productivity-377410/jobs/clickup-sync:run`
   - **HTTP method:** POST
   - **Auth header:** OAuth token → `clickup-sync-sa`
4. Click **Create**

### Cron schedule

| Expression | Meaning |
|------------|---------|
| `0 6 * * *` | Every day at 6:00 AM |
| `0 */4 * * *` | Every 4 hours |
| `0 9 * * 1-5` | Weekdays at 9:00 AM |

---

## 9. IAM Permissions Summary

### Service account (`clickup-sync-sa`)

| Role | Purpose |
|------|---------|
| `BigQuery Data Editor` | Read/write BigQuery tables |
| `BigQuery Job User` | Run BigQuery query jobs |
| `Secret Manager Secret Accessor` | Read secrets at runtime |
| `Cloud Run Invoker` | Allows Cloud Scheduler to trigger the job |
| `Artifact Registry Reader` | Pull the container image from Artifact Registry |

### User account (`dev@atidiv.com`)

| Role | Purpose |
|------|---------|
| `Artifact Registry Administrator` | Create/delete repos, push/delete/pull images |
| `Logs Viewer` | View Cloud Run Job execution logs |
| `Cloud Run Viewer` | View jobs and executions in Console |
| `Cloud Scheduler Admin` | Create and manage scheduler jobs |
