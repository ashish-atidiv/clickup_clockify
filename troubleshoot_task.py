#!/usr/bin/env python3
"""
Diagnose why a ClickUp task wasn't synced to Clockify.

Usage:
    python troubleshoot_task.py <clickup_task_id>
    python troubleshoot_task.py abc123xy,def456yz
"""

import sys
import os
import argparse
import datetime
import requests

# ── Local credentials (same pattern as main.py) ──────────────────────────────
_creds = os.path.join(os.path.dirname(__file__), "productivity.json")
if os.path.exists(_creds):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _creds

from envyaml import EnvYAML
from decouple import config as env_config
from google.cloud import bigquery

# ── Config ────────────────────────────────────────────────────────────────────
_cfg = EnvYAML("config.yaml")
DEV_MODE = _cfg.get("dev_mode", False)
REJECTED_SPACE_IDS = [str(x) for x in _cfg.get("prod.rejected_clickup_space_ids", [])]
LOOKBACK_DAYS = _cfg.get("prod.incremental_loading.lookback_days", 1)
BUFFER_SECONDS = _cfg.get("prod.incremental_loading.buffer_seconds", 14400)
HARDCODED_EXCLUDED = {"12ck3ph"}

GCP_PROJECT = "productivity-377410"
BQ_DATASET = "tickets_dataset" if not DEV_MODE else "_tickets_dataset"
TABLE_PREFIX = "_" if DEV_MODE else ""

CLICKUP_TOKEN = env_config("CLICKUP_TOKEN")
CLICKUP_BASE = "https://api.clickup.com/api/v2"
CLICKUP_HEADER = {"Authorization": CLICKUP_TOKEN, "Content-Type": "application/json"}

BQ_CLIENT = bigquery.Client(project=GCP_PROJECT)

# ── Terminal helpers ──────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def ok(msg):       print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):     print(f"  {RED}✗{RESET}  {msg}")
def warn(msg):     print(f"  {YELLOW}⚠{RESET}  {msg}")
def info(msg):     print(f"  {CYAN}→{RESET}  {msg}")
def header(title): print(f"\n{BOLD}{'─' * 60}\n  {title}\n{'─' * 60}{RESET}")
def dim(msg):      print(f"  {DIM}{msg}{RESET}")
def blank():       print()


# ── BQ helpers ────────────────────────────────────────────────────────────────
def bq_query(sql):
    try:
        return BQ_CLIENT.query(sql).result().to_dataframe()
    except Exception as e:
        return None, str(e)


def bq_scalar(sql):
    try:
        df = BQ_CLIENT.query(sql).result().to_dataframe()
        if df.empty:
            return None, None
        return df.iloc[0, 0], None
    except Exception as e:
        return None, str(e)


# ── ClickUp API ───────────────────────────────────────────────────────────────
def get_clickup_task(task_id):
    url = f"{CLICKUP_BASE}/task/{task_id}"
    try:
        r = requests.get(url, headers=CLICKUP_HEADER, timeout=10)
        if r.status_code == 200:
            return r.json(), None
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return None, str(e)


# ── Finding helpers ───────────────────────────────────────────────────────────
def add_finding(findings, status, label, detail, next_step=None):
    findings.append({"status": status, "label": label, "detail": detail, "next_step": next_step})


# ── Checks ────────────────────────────────────────────────────────────────────

def gate_already_synced(task_id, findings):
    table = f"`{GCP_PROJECT}.{BQ_DATASET}.{TABLE_PREFIX}clockify_task`"
    # Match both the normal case (name starts with clickup_id) and the dedup
    # false-positive where the ClickUp ID was mistakenly stored in the id column.
    sql = (
        f"SELECT id AS clockify_id, name "
        f"FROM {table} "
        f"WHERE name LIKE '{task_id} ||%' OR id = '{task_id}' LIMIT 1"
    )
    result = bq_query(sql)

    if isinstance(result, tuple):
        _, err = result
        warn(f"Could not query BQ clockify_task: {err}")
        add_finding(findings, "warn", "Gate check", f"BQ query failed: {err}")
        return False
    df = result

    if df.empty:
        info("Not found in clockify_task — task is not synced yet. Running checks...")
        return False

    row = df.iloc[0]
    stale_id_in_id_col = (row["clockify_id"] == task_id)

    if stale_id_in_id_col:
        # ClickUp ID stored in the id column — dedup false-positive
        print(f"\n{YELLOW}{BOLD}  ⚠ Dedup false-positive detected{RESET}")
        warn(f"ClickUp task ID '{task_id}' is stored in clockify_task.id — "
             f"the sync dedup treats it as already synced and skips it.")
        warn(f"Name in that row: '{row['name']}'")
        add_finding(findings, "fail", "Dedup false-positive (RC-06)",
                    f"ClickUp ID '{task_id}' found in clockify_task.id column — "
                    f"causes the sync to permanently skip this task.",
                    f"Delete the stale row and re-run the sync:\n"
                    f"    DELETE FROM {table} WHERE id = '{task_id}';\n"
                    f"Then: python main.py --lookback-days 1")
        return True  # stop further checks — root cause is identified

    print(f"\n{GREEN}{BOLD}  ✓ Already synced{RESET}")
    ok(f"Clockify task id  : {row['clockify_id']}")
    ok(f"Clockify task name: {row['name']}")
    add_finding(findings, "ok", "Already synced",
                f"Clockify task {row['clockify_id']} — '{row['name']}'")
    return True


def check_config(findings):
    header("CHECK 1 — Configuration")

    if DEV_MODE:
        fail("dev_mode is TRUE — syncing to dev Clockify workspace. BQ tables use '_' prefix.")
        add_finding(findings, "fail", "dev_mode",
                    "dev_mode is True — all syncs go to the dev workspace.",
                    "Set dev_mode: False in config.yaml and redeploy the Docker image.")
    else:
        ok("dev_mode is False — production mode active.")
        add_finding(findings, "ok", "dev_mode", "Production mode active.")

    info(f"Rejected space IDs : {REJECTED_SPACE_IDS or 'none'}")
    info(f"Incremental window : lookback_days={LOOKBACK_DAYS}, buffer_seconds={BUFFER_SECONDS}")


def check_hardcoded_exclusion(task_id, findings):
    header("CHECK 2 — Hardcoded Exclusion (RC-04)")

    if task_id in HARDCODED_EXCLUDED:
        fail(f"Task ID '{task_id}' is hardcoded as excluded in main.py and will never sync.")
        add_finding(findings, "fail", "Hardcoded exclusion",
                    f"Task '{task_id}' is in the hardcoded exclusion set in main.py.",
                    "Remove the task ID from the exclusion filter in main.py:clickup_tasks().")
        return True

    ok(f"Task ID '{task_id}' is not hardcoded as excluded.")
    add_finding(findings, "ok", "Hardcoded exclusion", "Not excluded.")
    return False


def check_clickup_api(task_id, findings):
    header("CHECK 3 — ClickUp API (task exists?)")

    task, err = get_clickup_task(task_id)
    if err:
        fail(f"Could not fetch task from ClickUp API: {err}")
        add_finding(findings, "fail", "ClickUp API",
                    f"API error: {err}",
                    "Verify the task ID is correct and CLICKUP_TOKEN is valid.")
        return None

    name        = task.get("name", "—")
    space_id    = task.get("space", {}).get("id", "")
    space_name  = task.get("space", {}).get("name", "—")
    list_id     = task.get("list", {}).get("id", "")
    list_name   = task.get("list", {}).get("name", "—")
    folder      = task.get("folder", {})
    in_folder   = not folder.get("hidden", True)
    folder_name = folder.get("name", "—") if in_folder else None

    date_created_ms = task.get("date_created")
    date_updated_ms = task.get("date_updated")

    ok(f"Task found: '{name}'")
    info(f"Space : {space_name} (id={space_id})")
    info(f"List  : {list_name} (id={list_id})")
    add_finding(findings, "ok", "ClickUp API", f"Task '{name}' exists (space={space_name}, list={list_name}).")

    if in_folder:
        warn(f"List '{list_name}' is inside folder '{folder_name}'. The sync only fetches folderless lists (RC-02).")
        add_finding(findings, "fail", "List in folder",
                    f"List '{list_name}' is nested inside folder '{folder_name}'. Folderless endpoint is used.",
                    f"Move list '{list_name}' out of the folder in ClickUp so it becomes a direct space list, "
                    f"OR extend get_clickup_lists() in utils to also call the /folder/{{id}}/list endpoint.")
    else:
        ok("List is folderless — visible to the sync.")
        add_finding(findings, "ok", "Folder check", "List is folderless.")

    if space_id in REJECTED_SPACE_IDS:
        fail(f"Space ID {space_id} is in rejected_clickup_space_ids — no lists are fetched from this space (RC-01).")
        add_finding(findings, "fail", "Rejected space",
                    f"Space '{space_name}' ({space_id}) is in the rejected list.",
                    f"Remove {space_id} from prod.rejected_clickup_space_ids in config.yaml and redeploy.")
    else:
        ok(f"Space '{space_name}' is not rejected.")
        add_finding(findings, "ok", "Rejected space", "Space is not in the rejected list.")

    created_dt = updated_dt = None
    if date_created_ms:
        created_dt = datetime.datetime.fromtimestamp(int(date_created_ms) / 1000, tz=datetime.timezone.utc)
        info(f"Created : {created_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    if date_updated_ms:
        updated_dt = datetime.datetime.fromtimestamp(int(date_updated_ms) / 1000, tz=datetime.timezone.utc)
        info(f"Updated : {updated_dt.strftime('%Y-%m-%d %H:%M UTC')}")

    return {
        "name": name, "space_id": space_id, "space_name": space_name,
        "list_id": list_id, "list_name": list_name,
        "in_folder": in_folder,
        "created_dt": created_dt, "updated_dt": updated_dt,
    }


def check_bq_task(task_id, findings):
    header("CHECK 4 — BigQuery: clickup_task table")

    table = f"`{GCP_PROJECT}.{BQ_DATASET}.{TABLE_PREFIX}clickup_task`"
    sql = (
        f"SELECT id, name, list_id, list_name, pull_date FROM {table} "
        f"WHERE id = '{task_id}' ORDER BY pull_date DESC LIMIT 1"
    )
    result = bq_query(sql)

    if isinstance(result, tuple):
        _, err = result
        warn(f"Could not query BQ clickup_task: {err}")
        add_finding(findings, "warn", "BQ clickup_task", f"Query failed: {err}")
        return None
    df = result

    if df.empty:
        fail("Task NOT found in BQ clickup_task — never fetched from ClickUp.")
        add_finding(findings, "fail", "BQ clickup_task",
                    "Task absent from clickup_task — was never pulled from the ClickUp API.")
        return None

    row = df.iloc[0]
    ok(f"Task found in BQ clickup_task (last pull: {row['pull_date']}).")
    info(f"list_id={row['list_id']}, list_name={row['list_name']}")
    add_finding(findings, "ok", "BQ clickup_task", f"Task present, last pulled at {row['pull_date']}.")
    return row


def check_time_window(task_details, findings):
    header("CHECK 5 — Incremental Time Window (RC-03)")

    sql = (
        f"SELECT MAX(pull_date) AS last_pull "
        f"FROM `{GCP_PROJECT}.{BQ_DATASET}.{TABLE_PREFIX}clickup_task`"
    )
    last_pull, err = bq_scalar(sql)

    if err:
        warn(f"Could not determine last pull date: {err}")
        add_finding(findings, "warn", "Time window", f"Could not read last pull date: {err}")
        return

    if last_pull is None:
        info(f"BQ clickup_task is empty — first run will use lookback_days={LOOKBACK_DAYS}.")
        add_finding(findings, "warn", "Time window", "No prior sync found — table is empty.")
        return

    if isinstance(last_pull, str):
        last_pull = datetime.datetime.fromisoformat(last_pull.replace("Z", "+00:00"))
    elif hasattr(last_pull, "to_pydatetime"):
        last_pull = last_pull.to_pydatetime()
    if hasattr(last_pull, "tzinfo") and last_pull.tzinfo is None:
        last_pull = last_pull.replace(tzinfo=datetime.timezone.utc)

    cutoff = last_pull - datetime.timedelta(seconds=BUFFER_SECONDS)
    ok(f"Last sync : {last_pull.strftime('%Y-%m-%d %H:%M UTC')}")
    info(f"Cutoff    : {cutoff.strftime('%Y-%m-%d %H:%M UTC')} (last sync − {BUFFER_SECONDS}s buffer)")

    if not task_details:
        return

    ref_dt = task_details.get("updated_dt") or task_details.get("created_dt")
    if not ref_dt:
        return

    if ref_dt < cutoff:
        days_behind = (cutoff - ref_dt).days + 1
        fail(f"Task last updated {ref_dt.strftime('%Y-%m-%d %H:%M UTC')} — {days_behind}d before cutoff.")
        add_finding(findings, "fail", "Time window",
                    f"Task last updated {ref_dt.strftime('%Y-%m-%d %H:%M UTC')}, "
                    f"which is {days_behind} day(s) before the effective cutoff.",
                    f"Run a backfill: python main.py --lookback-days {days_behind + 1}")
    else:
        ok(f"Task last updated {ref_dt.strftime('%Y-%m-%d %H:%M UTC')} — within the sync window.")
        add_finding(findings, "ok", "Time window", "Task is within the incremental window.")


def check_clockify_project(list_id, findings):
    header("CHECK 6 — Clockify Project mapping (RC-05)")

    table = f"`{GCP_PROJECT}.{BQ_DATASET}.{TABLE_PREFIX}clockify_project`"
    sql = (
        f"SELECT id AS clockify_project_id, name "
        f"FROM {table} WHERE note = '{list_id}' LIMIT 1"
    )
    result = bq_query(sql)

    if isinstance(result, tuple):
        _, err = result
        warn(f"Could not query BQ clockify_project: {err}")
        add_finding(findings, "warn", "Clockify project", f"Query failed: {err}")
        return None
    df = result

    if df.empty:
        fail(f"No Clockify project mapped to list_id='{list_id}' (RC-05).")
        add_finding(findings, "fail", "Clockify project",
                    f"No Clockify project exists for ClickUp list {list_id}. Tasks in this list are silently skipped.",
                    "Create the Clockify project manually with the list ID in its Note field and the correct Client, "
                    "then re-run the sync. Or fix the upstream cause (e.g. list-in-folder, RC-02) and let the next "
                    "sync create it automatically.")
        return None

    row = df.iloc[0]
    ok(f"Clockify project '{row['name']}' found (id={row['clockify_project_id']}).")
    add_finding(findings, "ok", "Clockify project", f"Project '{row['name']}' mapped correctly.")
    return row


def check_clockify_task_missing(task_id, findings):
    header("CHECK 7 — BigQuery: clockify_task table (RC-06/07)")

    table = f"`{GCP_PROJECT}.{BQ_DATASET}.{TABLE_PREFIX}clockify_task`"
    sql = (
        f"SELECT id AS clockify_id, name "
        f"FROM {table} WHERE name LIKE '{task_id} ||%' LIMIT 1"
    )
    result = bq_query(sql)

    if isinstance(result, tuple):
        _, err = result
        warn(f"Could not query BQ clockify_task: {err}")
        add_finding(findings, "warn", "BQ clockify_task", f"Query failed: {err}")
        return

    df = result
    if df.empty:
        warn("Task not found in BQ clockify_task — creation either failed or hasn't run yet.")
        add_finding(findings, "warn", "BQ clockify_task",
                    "No clockify_task record. Task creation on Clockify may have failed silently (RC-07).",
                    "Check Cloud Run logs for '[ERROR] Task not created on Clockify' or 'clickup_tasks error'. "
                    "Then re-run the sync after fixing the root cause above.")
    else:
        row = df.iloc[0]
        ok(f"BQ clockify_task record found: '{row['name']}' (id={row['clockify_id']}).")
        warn("BQ has a record but the task may be missing from Clockify UI — dedup false-positive (RC-06).")
        add_finding(findings, "warn", "BQ clockify_task (dedup)",
                    f"clockify_task row exists (id={row['clockify_id']}) but task may not exist in Clockify UI.",
                    f"Delete the stale BQ record so the next sync recreates it:\n"
                    f"    DELETE FROM {table} WHERE name LIKE '{task_id} ||%';")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(task_id, findings):
    STATUS_ICON = {"ok": f"{GREEN}✓{RESET}", "fail": f"{RED}✗{RESET}",
                   "warn": f"{YELLOW}⚠{RESET}", "info": f"{CYAN}→{RESET}"}

    print(f"\n{BOLD}{'═' * 60}")
    print(f"  SUMMARY — {task_id}")
    print(f"{'═' * 60}{RESET}")

    for f in findings:
        icon = STATUS_ICON.get(f["status"], " ")
        print(f"  {icon}  {f['label']}: {DIM}{f['detail']}{RESET}")

    # Next steps — only failures and actionable warnings
    actionable = [f for f in findings if f.get("next_step") and f["status"] in ("fail", "warn")]
    if not actionable:
        print(f"\n  {GREEN}{BOLD}No action needed.{RESET}")
        return

    print(f"\n{BOLD}  NEXT STEPS{RESET}")
    for i, f in enumerate(actionable, 1):
        print(f"\n  {BOLD}{i}. {f['label']}{RESET}")
        for line in f["next_step"].splitlines():
            print(f"     {line}")


# ── Per-task runner ───────────────────────────────────────────────────────────

def troubleshoot_one(task_id):
    findings = []

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  Task ID: {task_id}{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")

    header("GATE — Already synced?")
    if gate_already_synced(task_id, findings):
        print_summary(task_id, findings)
        return

    excluded = check_hardcoded_exclusion(task_id, findings)
    if excluded:
        print_summary(task_id, findings)
        return

    task_details = check_clickup_api(task_id, findings)
    bq_row = check_bq_task(task_id, findings)
    check_time_window(task_details, findings)

    list_id = None
    if task_details:
        list_id = task_details.get("list_id")
    elif bq_row is not None:
        list_id = str(bq_row.get("list_id", ""))

    if list_id:
        check_clockify_project(list_id, findings)

    check_clockify_task_missing(task_id, findings)

    print_summary(task_id, findings)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Diagnose why ClickUp tasks were not synced to Clockify.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python troubleshoot_task.py abc123xy\n"
            "  python troubleshoot_task.py abc123xy,def456yz,ghi789wv"
        ),
    )
    parser.add_argument(
        "task_ids",
        help="One or more ClickUp task IDs, comma-separated (from the task URL: /t/<ID>)",
    )
    args = parser.parse_args()

    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()]

    print(f"\n{BOLD}ClickUp → Clockify Sync Troubleshooter{RESET}")
    check_config(findings := [])

    for task_id in task_ids:
        troubleshoot_one(task_id)

    print(f"\n{DIM}Done — {len(task_ids)} task(s) checked.{RESET}\n")


if __name__ == "__main__":
    main()
