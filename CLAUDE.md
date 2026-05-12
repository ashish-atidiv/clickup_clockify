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

## Deploying to GCP

### Cloud Function (recommended — no Docker needed, max 60 min timeout)

1. Add `functions-framework==3.5.0` to `requirements.txt`
2. Add an HTTP entry point to `main.py`:
```python
import functions_framework

@functions_framework.http
def run(request):
    lookback_days = request.args.get('lookback_days', default=None, type=int)
    buffer_seconds = request.args.get('buffer_seconds', default=None, type=int)
    try:
        main(lookback_days=lookback_days, buffer_seconds=buffer_seconds)
        return "Sync complete.", 200
    except Exception as e:
        return f"Sync failed: {e}", 500
```
3. Remove the `os.environ['GOOGLE_APPLICATION_CREDENTIALS']` line from `main.py` — Cloud Function uses the attached service account automatically.

Deploy:
```bash
gcloud functions deploy clickup-sync \
  --gen2 --runtime python311 --region us-central1 \
  --source . --entry-point run --trigger-http \
  --no-allow-unauthenticated \
  --service-account YOUR_SA@productivity-377410.iam.gserviceaccount.com \
  --set-secrets CLICKUP_TOKEN=CLICKUP_TOKEN:latest \
  --set-secrets CLICKUP_TEAM_ID=CLICKUP_TEAM_ID:latest \
  --set-secrets CLOCKIFY_TOKEN=CLOCKIFY_TOKEN:latest \
  --set-secrets CLOCKIFY_ATIDIV_WORKSPACE_ID=CLOCKIFY_ATIDIV_WORKSPACE_ID:latest \
  --memory 512MB --timeout 3600s
```

### Cloud Run Job (for syncs that may exceed 60 min)

Requires a `Dockerfile`. Same secret/service-account pattern. See Cloud Run Jobs docs.

### Scheduling (Cloud Scheduler)

```bash
gcloud scheduler jobs create http clickup-sync-daily \
  --schedule "0 6 * * *" \
  --uri "https://us-central1-cloudfunctions.googleapis.com/v2/projects/productivity-377410/locations/us-central1/functions/clickup-sync" \
  --http-method POST \
  --oidc-service-account-email YOUR_SA@productivity-377410.iam.gserviceaccount.com \
  --location us-central1
```

### Required IAM roles for the service account

- `roles/bigquery.dataEditor` — read/write BQ tables
- `roles/secretmanager.secretAccessor` — read secrets
