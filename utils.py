import endpoints as api
import settings as env
import requests
import pandas as pd
import json
import bigquery_utils as bq
import db
import logging
from datetime import datetime, date, timedelta
import time
from envyaml import EnvYAML

logger = logging.getLogger(__name__)

CONFIG = EnvYAML('config.yaml').get('prod')


# GET CLICKUP SPACES
# ---------------------------------------------------------------
def get_clickup_spaces():
    try:
        url = api.clickup_spaces
        response = requests.get(url, headers=api.clickup_header)
        if response.status_code == 200:
            return response.json().get('spaces')
        else:
            logger.error("get_clickup_spaces failed [%s]: %s", response.status_code, response.text)
    except Exception as e:
        logger.error("get_clickup_spaces exception: %s", e)


# GET CLICKUP LISTS (folderless + folder-based)
# ---------------------------------------------------------------
def get_clickup_lists(space_id):
    lists = []

    try:
        url = api.clickup_folderless_list.format(space_id=space_id)
        response = requests.get(url, headers=api.clickup_header)
        if response.status_code == 200:
            lists = response.json().get('lists', [])
        else:
            logger.warning("get_clickup_lists (folderless) [%s] space=%s: %s",
                           response.status_code, space_id, response.text)
    except Exception as e:
        logger.error("get_clickup_lists (folderless) exception for space=%s: %s", space_id, e)

    folders = []
    try:
        url = api.clickup_folders.format(space_id=space_id)
        response = requests.get(url, headers=api.clickup_header)
        if response.status_code == 200:
            folders = response.json().get('folders', [])
        else:
            logger.warning("get_clickup_lists (folders) [%s] space=%s: %s",
                           response.status_code, space_id, response.text)
    except Exception as e:
        logger.error("get_clickup_lists (folders) exception for space=%s: %s", space_id, e)

    try:
        for elm in folders:
            folder_id = elm.get('id')
            url = api.clickup_list_with_folder.format(folder_id=folder_id)
            response = requests.get(url, headers=api.clickup_header)
            if response.status_code == 200:
                lists.extend(response.json().get('lists', []))
            else:
                logger.warning("get_clickup_lists (folder lists) [%s] folder=%s: %s",
                               response.status_code, folder_id, response.text)
    except Exception as e:
        logger.error("get_clickup_lists (folder lists) exception: %s", e)

    return lists


# GET CLOCKIFY CLIENTS
# ---------------------------------------------------------------
def get_clockify_clients():
    try:
        response = requests.get(url=api.clockify_client_api, headers=api.clockify_header)
        logger.debug("get_clockify_clients response: %s", response.status_code)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error("get_clockify_clients failed [%s]: %s", response.status_code, response.text)
    except Exception as e:
        logger.error("get_clockify_clients exception: %s", e)


# CREATE CLOCKIFY CLIENTS
# ---------------------------------------------------------------
def create_clockify_client(client_name, client_note):
    try:
        payload = json.dumps({"name": client_name, "note": client_note})
        response = requests.post(url=api.clockify_client_api, headers=api.clockify_header, data=payload)
        if response.status_code == 201:
            logger.info("Clockify client created: %s", client_name)
            return pd.json_normalize(json.loads(response.text))
        elif response.status_code == 400 and response.json().get('code') == 501:
            logger.warning("Clockify client already exists: '%s'", client_name)
        else:
            logger.error("create_clockify_client failed for '%s' [%s]: %s",
                         client_name, response.status_code, response.text)
    except Exception as e:
        logger.error("create_clockify_client exception: %s", e)


# CREATE CLOCKIFY PROJECTS
# ---------------------------------------------------------------
def create_clockify_projects(project_name, project_note, client_id):
    try:
        payload = json.dumps({
            "name": project_name,
            "note": project_note,
            "clientId": client_id,
        })
        response = requests.post(url=api.clockify_project_api, headers=api.clockify_header, data=payload)
        if response.status_code == 201:
            logger.info("Clockify project created: %s", project_name)
            return response.status_code, response.json()
        elif response.status_code == 400 and response.json().get('code') == 501:
            logger.warning("Clockify project already exists on API (not in BQ tracking): '%s'", project_name)
            return 501, {}
        else:
            logger.error("create_clockify_projects failed for '%s' [%s]: %s",
                         project_name, response.status_code, response.text)
            return response.status_code, {}
    except Exception as e:
        logger.error("create_clockify_projects exception: %s", e)


# GET CLOCKIFY PROJECTS
# ---------------------------------------------------------------
def get_clockify_projects():
    try:
        response = requests.get(url=api.clockify_project_api, headers=api.clockify_header,
                                params=api.clockify_params)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error("get_clockify_projects failed [%s]: %s", response.status_code, response.text)
    except Exception as e:
        logger.error("get_clockify_projects exception: %s", e)


# ---------------------------------------------------------------
def get_space_client_mapping():
    client = get_clockify_clients()
    return {elm['note']: elm['id'] for elm in client}


# ---------------------------------------------------------------
def get_clickup_tasks(list_id, _unix_ts):
    all_tasks = []
    page = 0

    while True:
        response = requests.get(
            url=api.clickup_task.format(list_id=list_id, page_no=page, date_created_gt=_unix_ts),
            headers=api.clickup_header,
        )
        if response.status_code == 200:
            tasks = response.json()['tasks']
            if tasks:
                all_tasks.extend(tasks)
                page += 1
            else:
                break
        else:
            logger.error("get_clickup_tasks failed [%s] list=%s page=%s: %s",
                         response.status_code, list_id, page, response.text)
            break

    return pd.json_normalize(all_tasks)


# ---------------------------------------------------------------
def fetch_all_clickup_tasks(lookback_days=None, buffer_seconds=None):
    """
    Fetch all ClickUp tasks with configurable incremental loading.

    Args:
        lookback_days (int, optional): Days to look back on first run. Overrides config.yaml.
        buffer_seconds (int, optional): Overlap buffer subtracted from last pull_date. Overrides config.yaml.

    Returns:
        pd.DataFrame: All fetched tasks.
    """
    master_tasks_df = pd.DataFrame()
    spaces = get_clickup_spaces()
    rejected_spaces = get_clickup_rejected_spaces()
    pull_date = current_date_time()

    config_incremental = CONFIG.get('incremental_loading', {})
    effective_lookback_days = lookback_days if lookback_days is not None else config_incremental.get('lookback_days', 1)
    effective_buffer_seconds = buffer_seconds if buffer_seconds is not None else config_incremental.get('buffer_seconds', 14400)

    logger.info("Incremental config: lookback_days=%s, buffer_seconds=%s",
                effective_lookback_days, effective_buffer_seconds)

    if lookback_days is not None:
        # Explicit CLI override — ignore prior pull_date and fetch from N days ago.
        forced_date = (datetime.now() - timedelta(days=effective_lookback_days)).strftime('%Y-%m-%d %H:%M:%S')
        logger.info("--lookback-days override: fetching from %s (%d days back, ignoring prior pull_date)",
                    forced_date, effective_lookback_days)
        unix_ts = get_unix_timestamp(forced_date, buffer_seconds=0)
    else:
        db_pull_date = bq.gcp2df(
            "select max(pull_date) from `{}.{}.{}`".format(bq.gcp_project, bq.bq_dataset, db.CLICKUP_TASK)
        ).values[0][0]

        if not db_pull_date:
            db_pull_date = (datetime.now() - timedelta(days=effective_lookback_days)).strftime('%Y-%m-%d %H:%M:%S')
            logger.info("No previous pull_date found. Using lookback: %s days → %s", effective_lookback_days, db_pull_date)
        else:
            logger.info("Previous pull_date: %s. Applying %ss buffer.", db_pull_date, effective_buffer_seconds)

        unix_ts = get_unix_timestamp(db_pull_date, buffer_seconds=effective_buffer_seconds)

    for spc in spaces:
        space_name = spc.get('name', spc['id'])
        logger.info("── Space: %s", space_name)
        space_list = get_clickup_lists(spc['id']) if spc['id'] not in rejected_spaces else []
        if space_list:
            for lst in space_list:
                tasks_df = get_clickup_tasks(lst['id'], unix_ts)
                master_tasks_df = pd.concat([master_tasks_df, tasks_df])
                logger.info("     → list '%-40s'  fetched: %d  total: %d",
                            lst.get('name', lst['id']), len(tasks_df), len(master_tasks_df))
        else:
            logger.debug("     → skipped (rejected or no lists).")

        logger.info("   finished space '%s'.", space_name)

    logger.info("Parsed all spaces. Total tasks since last pull: %d", len(master_tasks_df))

    standardize_column(master_tasks_df)
    master_tasks_df['pull_date'] = pull_date

    return master_tasks_df


# ---------------------------------------------------------------
def standardize_column(df):
    df.columns = [c.replace('.', '_') for c in df.columns]
    return df


# ---------------------------------------------------------------
def get_clockify_clients_bq():
    try:
        sql = "select * from {}.{}.{}".format(bq.gcp_project, bq.bq_dataset, db.CLOCKIFY_CLIENT)
        return bq.gcp2df(sql)
    except Exception as e:
        logger.error("get_clockify_clients_bq exception: %s", e)
        return pd.DataFrame()


# ---------------------------------------------------------------
def create_clockify_task(proj_id, task_name, clickup_list_id, clickup_task_id):
    try:
        url = api.clockify_task_api.format(project_id=proj_id)
        payload = json.dumps({"name": task_name})
        response = requests.post(url, headers=api.clockify_header, data=payload)

        if response.status_code == 201:
            resp = response.json()
            resp['clickup_list_id'] = clickup_list_id
            resp['clickup_task_id'] = clickup_task_id
            return resp
        else:
            resp = response.json()
            if response.status_code == 400 and resp.get("code") == 501:
                logger.warning("Clockify task already exists: '%s'", task_name)
                return {"id": clickup_task_id}
            logger.error("create_clockify_task failed for clickup_task=%s [%s]: %s",
                         clickup_task_id, response.status_code, resp)
            return None
    except Exception as e:
        logger.error("create_clockify_task exception for clickup_task=%s: %s", clickup_task_id, e)


# ---------------------------------------------------------------
def get_clockify_tasks(project_id):
    """Returns list of task dicts for a given Clockify project."""
    url = api.clockify_task_api.format(project_id=project_id) + '?page-size=5000&is-active=true'
    response = requests.get(url, headers=api.clockify_header)
    if response.status_code == 200:
        return json.loads(response.text)
    else:
        logger.error("get_clockify_tasks failed [%s] project=%s: %s",
                     response.status_code, project_id, response.text)
        return []


# ---------------------------------------------------------------
def get_unix_timestamp(_db_date, buffer_seconds=0):
    """
    Convert a date string to a Unix timestamp in milliseconds with an optional second buffer.

    Args:
        _db_date (str): Date string "%Y-%m-%d %H:%M:%S"
        buffer_seconds (int): Seconds to subtract from the date (overlap buffer). Default 0.

    Returns:
        int: Unix timestamp in milliseconds.
    """
    last_cycle = datetime.strptime(_db_date, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=buffer_seconds)
    return int(time.mktime(last_cycle.timetuple()) * 1000)


# ---------------------------------------------------------------
def DELETE_ALL_CLOCKIFY_TASK():
    projects = get_clockify_projects()
    for prj in projects:
        try:
            project_id = prj['id']
            if project_id == '63e23e4c192143097fc8d3ea':
                continue
            tasks = get_clockify_tasks(project_id=project_id)
            logger.info("Deleting %d tasks for project %s", len(tasks), project_id)
            for elm in tasks:
                try:
                    delete_clockify_task(project_id=project_id, task_id=elm['id'])
                except Exception as e:
                    logger.error("delete_clockify_task error project=%s task=%s: %s",
                                 project_id, elm.get('id'), e)
            logger.info("Project cleaned: %s", prj['name'])
        except Exception as e:
            logger.error("DELETE_ALL_CLOCKIFY_TASK error project=%s: %s", prj.get('id'), e)


# ---------------------------------------------------------------
def delete_clockify_task(project_id, task_id):
    url = api.delete_clociky_task.format(projectId=project_id, taskId=task_id)
    response = requests.delete(url=url, headers=api.clockify_header)
    if response.status_code == 200:
        logger.info("Deleted Clockify task %s from project %s", task_id, project_id)
    else:
        logger.error("delete_clockify_task failed project=%s task=%s [%s]",
                     project_id, task_id, response.status_code)


# ---------------------------------------------------------------
def update_task_name(project_id, task_id, clickup_parent_id, clickup_child_id, child_name, parent_name):
    try:
        url = api.delete_clociky_task.format(projectId=project_id, taskId=task_id)
        payload = json.dumps({
            "name": "{}-{}-{}-{}".format(clickup_child_id, child_name, clickup_parent_id, parent_name)
        })
        response = requests.put(url=url, headers=api.clockify_header, data=payload)
        if response.status_code == 200:
            logger.info("Updated Clockify task %s", task_id)
        else:
            logger.error("update_task_name failed task=%s [%s]: %s",
                         task_id, response.status_code, response.text)
    except Exception as e:
        logger.error("update_task_name exception task=%s: %s", task_id, e)


# ---------------------------------------------------------------
def current_date_time():
    """Returns current datetime string: '2023-02-23 19:23:44'"""
    try:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        logger.error("current_date_time exception: %s", e)


# ---------------------------------------------------------------
def get_all_clockify_tasks():
    projects = get_clockify_projects()
    master_df = pd.DataFrame()

    for prj in projects:
        try:
            project_id = prj['id']
            if project_id == '63e23e4c192143097fc8d3ea':
                continue
            logger.info("Fetching tasks for project %s (%s)", prj['name'], project_id)
            tasks = get_clockify_tasks(project_id=project_id)
            task_df = pd.DataFrame(tasks)
            task_df['clickup_task_id'] = None
            task_df['clickup_list_id'] = None
            task_df['pull_date'] = current_date_time()
            logger.info("Added %d tasks from project %s", len(task_df), prj['name'])
            master_df = pd.concat([master_df, task_df])
        except Exception as e:
            logger.error("get_all_clockify_tasks error for project %s: %s", prj.get('id'), e)

    return master_df


# ---------------------------------------------------------------
def get_clickup_rejected_spaces():
    try:
        rejected_list = CONFIG.get('rejected_clickup_space_ids')
        return [str(x) for x in rejected_list]
    except Exception as e:
        logger.error("get_clickup_rejected_spaces exception: %s", e)
        return []


# ---------------------------------------------------------------
def dump_new_clickup_list_to_bq(all_lists):
    try:
        if not all_lists:
            return
        all_lists_df = pd.json_normalize(all_lists)
        if all_lists_df.empty:
            logger.warning("dump_new_clickup_list_to_bq: no columns found in list data.")
            return
        new_df = standardize_column(all_lists_df)
        new_df['pull_date'] = current_date_time()
        bq.df2gcp(new_df, db.CLICKUP_LIST, mode='replace')
    except Exception as e:
        logger.error("dump_new_clickup_list_to_bq exception: %s", e)


# ---------------------------------------------------------------
def dump_new_clockify_project_to_bq(_responses):
    """Append new project entries to clockify_projects table."""
    try:
        if not _responses:
            return
        all_lists_df = pd.json_normalize(_responses)
        if all_lists_df.empty:
            logger.warning("dump_new_clockify_project_to_bq: no columns in response data.")
            return
        project_df = standardize_column(all_lists_df)
        project_df.drop(axis=1, columns=['memberships'], inplace=True)
        project_df['pull_date'] = current_date_time()
        bq.df2gcp(project_df, db.CLOCKIFY_PROJECT, mode='append')
    except Exception as e:
        logger.error("dump_new_clockify_project_to_bq exception: %s", e)


# ---------------------------------------------------------------
def dump_new_clickup_space_to_bq(_responses_df, drop_col=[]):
    """Replace clickup_space table with current space data."""
    try:
        if _responses_df.empty:
            logger.warning("dump_new_clickup_space_to_bq: empty DataFrame, skipping.")
            return
        space_df = standardize_column(_responses_df)
        space_df['pull_date'] = current_date_time()
        db_columns = ['id', 'name', 'color', 'private', 'admin_can_manage',
                      'multiple_assignees', 'archived', 'pull_date']
        space_df = space_df[db_columns]
        bq.df2gcp(space_df, db.CLICKUP_SPACE, mode='replace')
    except Exception as e:
        logger.error("dump_new_clickup_space_to_bq exception: %s", e)
