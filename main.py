import os
import sys
import logging
import argparse
import pandas as pd

# Local development only — Cloud Function uses the attached service account automatically.
_creds = (
    "C:\\Users\\Ashish Agrawal\\Documents\\Codes\\codebase\\gcloud\\productivity.json"
)
if os.path.exists(_creds):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _creds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from utils import (
    get_clickup_spaces,
    get_clockify_clients_bq,
    create_clockify_client,
    dump_new_clickup_space_to_bq,
    get_space_client_mapping,
    get_clockify_projects,
    get_clickup_lists,
    get_clickup_rejected_spaces,
    create_clockify_projects,
    dump_new_clickup_list_to_bq,
    fetch_all_clickup_tasks,
    standardize_column,
    create_clockify_task,
    current_date_time,
)
from bigquery_utils import gcp2df, df2gcp
import db
import bigquery_utils as bq


def parse_arguments():
    """Parse command-line arguments for incremental loading configuration."""
    parser = argparse.ArgumentParser(
        description="""ClickUp to Clockify Data Sync with Incremental Loading

Syncs ClickUp Spaces/Lists/Tasks to Clockify Clients/Projects/Tasks and stores data in BigQuery.

Incremental loading controls how much historical data is fetched:
- First run:       uses --lookback-days to fetch tasks from N days ago
- Subsequent runs: uses --buffer-seconds as an overlap buffer from last sync

For detailed usage, see USAGE.md""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python main.py                        Use defaults from config.yaml
  python main.py --lookback-days 7      Fetch tasks from last 7 days (first run only)
  python main.py --buffer-seconds 3600  Use 1-hour buffer on subsequent runs
  python main.py -d 30 -b 7200          30-day lookback, 2-hour buffer

Configuration priority: CLI args > config.yaml > hardcoded defaults (1 day, 14400 seconds)""",
    )
    parser.add_argument(
        "--lookback-days",
        "-d",
        type=int,
        default=None,
        metavar="DAYS",
        help="Days to look back on first run (overrides config.yaml).",
    )
    parser.add_argument(
        "--buffer-seconds",
        "-b",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Buffer in seconds subtracted from last pull_date (overrides config.yaml).",
    )
    return parser.parse_args()


def clickup_spaces():
    """Sync ClickUp Spaces → Clockify Clients → BigQuery."""
    spaces_data = get_clickup_spaces()
    all_spaces = pd.DataFrame(spaces_data)

    clockify_clients = get_clockify_clients_bq()
    space_ids_in_clients = clockify_clients["clickup_space_id"].values.tolist()

    new_spaces = all_spaces[~all_spaces["id"].isin(space_ids_in_clients)]
    new_client_to_write_to_db = pd.DataFrame()

    pull_date = current_date_time()
    for _, elm in new_spaces.iterrows():
        try:
            clockify_client_df = create_clockify_client(
                elm["name"], client_note=elm["id"]
            )
            if clockify_client_df is None:
                # Already exists on Clockify but missing from BQ — warning already logged in utils
                continue
            clockify_client_df["clickup_space_id"] = elm["id"]
            clockify_client_df["pull_date"] = pull_date
            new_client_to_write_to_db = pd.concat(
                [new_client_to_write_to_db, clockify_client_df]
            )
        except Exception as e:
            logger.error(
                "Failed to create Clockify client for space '%s': %s", elm["name"], e
            )

    all_spaces["archived"] = all_spaces["archived"].astype("string")
    dump_new_clickup_space_to_bq(all_spaces)

    if not new_client_to_write_to_db.empty:
        df2gcp(new_client_to_write_to_db, db.CLOCKIFY_CLIENT, mode="append")

    logger.info(
        "%d new Clockify clients created. %d total spaces passed forward.",
        len(new_client_to_write_to_db),
        len(all_spaces),
    )
    return all_spaces


def clickup_list(all_spaces):
    """Sync ClickUp Lists → Clockify Projects → BigQuery."""
    space_client_mapping = get_space_client_mapping()
    clockify_projects = get_clockify_projects()
    list_ids_in_projects = [x["note"] for x in clockify_projects]
    rejected_clickup_list = get_clickup_rejected_spaces()

    all_lists = []
    success_response_list = []

    for _, spc in all_spaces.iterrows():
        space_lists = (
            get_clickup_lists(spc["id"])
            if spc["id"] not in rejected_clickup_list
            else []
        )

        for elm in space_lists:
            try:
                if elm["id"] not in list_ids_in_projects:
                    list_space_id = space_client_mapping[spc["id"]]
                    resp_code, json_response = create_clockify_projects(
                        elm["name"], project_note=elm["id"], client_id=list_space_id
                    )
                    if resp_code == 201:
                        success_response_list.append(json_response)
                        clockify_projects.append(json_response)
                else:
                    logger.warning(
                        "Clockify project already exists: '%s' (space: %s)",
                        elm.get("name"),
                        spc["name"],
                    )
                all_lists.append(elm)
            except Exception as e:
                logger.error(
                    "clickup_list error for list '%s' in space '%s': %s",
                    elm.get("name"),
                    spc.get("name"),
                    e,
                )

    dump_new_clickup_list_to_bq(all_lists)
    logger.info("%d new Clockify projects created.", len(success_response_list))
    return clockify_projects


def clickup_tasks(_all_clockify_projects, clickup_task_df):
    """Create Clockify Tasks from ClickUp tasks and write results to BigQuery."""
    clickup_task_df = clickup_task_df[clickup_task_df.id != "12ck3ph"]

    clickup_task_df.drop(
        axis=1,
        columns=["custom_fields", "dependencies", "group_assignees"],
        inplace=True,
        errors="ignore",
    )

    clickup_task_df["start_date"] = pd.to_numeric(
        clickup_task_df["start_date"], errors="coerce"
    ).astype("Int64")
    clickup_task_df["due_date"] = clickup_task_df["due_date"].astype("string")
    df2gcp(clickup_task_df, db.CLICKUP_TASK, mode="append")

    clickup_df = clickup_task_df[["id", "name", "list_id", "list_name"]]

    clockify_projects_lst = [
        {
            "clickup_id": x["note"],
            "clockify_project_id": x["id"],
            "clockify_project_name": x["name"],
        }
        for x in _all_clockify_projects
    ]
    project_df = pd.DataFrame(clockify_projects_lst)

    # Write full Clockify project list to BQ
    db_project = pd.DataFrame(_all_clockify_projects)
    db_project.drop(axis=1, columns=["memberships"], inplace=True)
    db_project["pull_date"] = current_date_time()
    df2gcp(db_project, db.CLOCKIFY_PROJECT, mode="replace")

    # Dedup: skip tasks already created in Clockify
    clockify_bq_task_list = []
    try:
        clockify_bq_task = gcp2df(
            "select distinct id from `{}.{}.{}`".format(
                bq.gcp_project, bq.bq_dataset, db.CLOCKIFY_TASK
            )
        ).values.tolist()
        clockify_bq_task_list = [x[0] for x in clockify_bq_task]
    except Exception as e:
        logger.error("Failed to fetch existing Clockify tasks from BQ: %s", e)

    clickup_trimmed_df = clickup_df[
        ~clickup_df["id"].isin(clockify_bq_task_list)
    ].reset_index()
    logger.info("%d new tasks to create on Clockify.", len(clickup_trimmed_df))

    new_task_created = []
    for _, elm in clickup_trimmed_df.iterrows():
        try:
            matched = project_df[project_df.clickup_id == elm["list_id"]][
                "clockify_project_id"
            ].values
            if not len(matched):
                continue
            clk_project_id = matched[0]

            if clk_project_id:
                logger.info("Creating task: %s", elm["name"])
                resp = create_clockify_task(
                    clk_project_id,
                    elm["id"] + " || " + elm["name"],
                    elm["list_id"],
                    elm["id"],
                )
                if resp:
                    new_task_created.append(resp)
                else:
                    logger.error("Task not created on Clockify: %s", elm["name"])
        except Exception as e:
            logger.error(
                "clickup_tasks error for task '%s' (list: %s): %s",
                elm.get("name"),
                elm.get("list_name"),
                e,
            )

    df_to_write = pd.DataFrame(new_task_created)
    if not df_to_write.empty:
        clockify_task_cols = ["id", "name", "list_id", "list_name"]
        cols = [c for c in clockify_task_cols if c in df_to_write.columns]
        df2gcp(df_to_write[cols], db.CLOCKIFY_TASK, mode="append")
    logger.info(
        "%d tasks written to clockify_task. %d new tasks found in ClickUp.",
        len(df_to_write),
        len(clickup_trimmed_df),
    )


def main(lookback_days=None, buffer_seconds=None):
    """Main ETL: ClickUp → BigQuery → Clockify sync."""
    logger.info("Sync started at %s", current_date_time())

    clients = clickup_spaces()
    projects = clickup_list(clients)

    clickup_task_df = fetch_all_clickup_tasks(
        lookback_days=lookback_days,
        buffer_seconds=buffer_seconds,
    )

    if len(clickup_task_df):
        clickup_tasks(projects, clickup_task_df)
    else:
        logger.info("No new tasks fetched since last pull.")

    logger.info("Sync complete.")


if __name__ == "__main__":
    args = parse_arguments()
    try:
        main(lookback_days=args.lookback_days, buffer_seconds=args.buffer_seconds)
    except Exception as e:
        logger.error("Sync failed: %s", e, exc_info=True)
        sys.exit(1)
