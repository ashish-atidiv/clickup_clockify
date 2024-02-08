import utils.endpoints as api
import settings as env
import requests
import pandas as pd 
import json 
import utils.bigquery_utils as bq
import utils.db as db
from  datetime import datetime, date, timedelta
import time
from envyaml import EnvYAML
CONFIG = EnvYAML('config.yaml').get('prod')

# GET CLICKUP SPACES
# ---------------------------------------------------------------
def get_clickup_spaces():
    try:
        spaces = {}
        url = api.clickup_spaces

        response = requests.get(url, headers=api.clickup_header)

        if response.status_code == 200:
            resp = response.json()
            spaces = resp.get('spaces')

            return spaces
        

    except Exception as e:
        print(str(e))


# # GET CLICKUP FOLDERS
# # ---------------------------------------------------------------
# def get_clickup_folders(space_id):
#     try:
#         folders = {}
#         url = api.clickup_folders.format(space_id=space_id)

#         response = requests.get(url, headers=api.clickup_header)

#         if response.status_code == 200:
#             resp = response.json()
#             folders = resp.get('folders')
#             print(folders)
#             return folders
        
#     except Exception as e:
#         print(str(e))




# GET CLICKUP LISTS
# ---------------------------------------------------------------
def get_clickup_lists(space_id):
    try:
        lists = []
        url = api.clickup_folderless_list.format(space_id=space_id)

        response = requests.get(url, headers=api.clickup_header)

        if response.status_code == 200:
            resp = response.json()
            lists = resp.get('lists')
            print(lists)
            #return lists
        else:
            #return lists
            pass

    except Exception as e:
        print(str(e))

    try:
        folders = []
        url = api.clickup_folders.format(space_id=space_id)

        response = requests.get(url, headers=api.clickup_header)

        if response.status_code == 200:
            resp = response.json()
            folders = resp.get('folders')
            #print(folders)
            #return folders
        else:
            #return lists
            pass

    except Exception as e:
        print(str(e))

    try:        
        for elm in folders:
            lists_1 = []
            folder_id = elm.get('id')
            url = api.clickup_list_with_folder.format(folder_id=folder_id)

            response = requests.get(url, headers=api.clickup_header)

            if response.status_code == 200:
                resp = response.json()
                lists_1 = resp.get('lists')
                #print(lists_1)
                lists.extend(lists_1)
                #print(lists)
                #return lists
            else:
                #return lists
                pass

    except Exception as e:
        print(str(e))
    return lists

# GET CLOCKIFY CLIENTS
# ---------------------------------------------------------------
def get_clockify_clients():
    try:
        
        response = requests.get(url=api.clockify_client_api, headers=api.clockify_header)
        print("client response",response)
        if response.status_code == 200:
            resp = response.json()
            return resp

    except Exception as e:
        print(str(e))


# CREATE CLOCKIFY CLIENTS
# ---------------------------------------------------------------
def create_clockify_client(client_name, client_note):
    ''' 
        returns if response.status_code == 201:
        print('client created - ', client_name)
        return pd.json_normalize( response)
    '''
    try:
        
        payload = json.dumps({
            "name": client_name,
            "note": client_note
        })

        response = requests.post(url=api.clockify_client_api, headers=api.clockify_header, data = payload)
        if response.status_code == 201:
            print('client created - ', client_name)
            return pd.json_normalize(json.loads(response.text))
        else:
            print('failed api. Err: ', response.text)

    except Exception as e:
        
        print(str(e))


# CREATE CLOCKIFY projects
# ---------------------------------------------------------------
def create_clockify_projects(project_name, project_note, client_id):
    try:
        
        payload = json.dumps({
            "name": project_name,
            "note": project_note,
            "clientId": client_id ## client_id of an existing client on Clockify
        })

        response = requests.post(url=api.clockify_project_api, headers=api.clockify_header, data = payload)

        if response.status_code == 201:
            print('project created - ', project_name)
            resp = response.json()
            return response.status_code, response.json()
        else:
            return 501
            print('failed api. Err: ', response.text)

    except Exception as e:
        
        print("create_clockify_projects ", str(e))


# GET CLOCKIFY projects
# ---------------------------------------------------------------
def get_clockify_projects():
    try:
        
        response = requests.get(url=api.clockify_project_api, headers=api.clockify_header,params = api.clockify_params)
        
        if response.status_code == 200:
            projects = response.json()
            return projects

    except Exception as e:
        
        print("create_clockify_projects ", str(e))


# 
# ---------------------------------------------------------------
def get_space_client_mapping():

    client = get_clockify_clients()
    mapping = {}

    for elm in client:
        mapping[elm['note']] = elm['id']

    return mapping

# 
# ---------------------------------------------------------------
def get_clickup_tasks(list_id, _unix_ts):

    all_tasks = []
    page = 0

    while True:

        response = requests.get(url=api.clickup_task.format(list_id=list_id, page_no=page, date_created_gt=_unix_ts), headers=api.clickup_header)

        if response.status_code == 200:
            resp = response.json()
            tasks = resp['tasks']

            if len(tasks) > 0:
                all_tasks.extend(tasks)
                page += 1
            else: 
                break

    tasks_df = pd.json_normalize(all_tasks)

    return tasks_df

# 
# ---------------------------------------------------------------
def fetch_all_clickup_tasks():
    
    master_tasks_df = pd.DataFrame()
    
    spaces = get_clickup_spaces()
    rejected_spaces = get_clickup_rejected_spaces()

    pull_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    db_pull_date = bq.gcp2df("select max(pull_date) from `{}.{}.{}`".format(bq.gcp_project, 
                                                                            bq.bq_dataset, 
                                                                            db.CLICKUP_TASK))
    db_pull_date = db_pull_date.values[0][0]

    if not db_pull_date:
        db_pull_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S') - timedelta(days=1)
    
    unix_ts = get_unix_timestamp(db_pull_date, timedelay=14400)

    for spc in spaces:
    
        space_list = get_clickup_lists(spc['id']) if spc['id'] not in rejected_spaces else list()
        if space_list:
            for lst in space_list:

                tasks_df = get_clickup_tasks(lst['id'], unix_ts)
                
                master_tasks_df =  pd.concat([master_tasks_df, tasks_df])

                print('{} Appended {} row to master_df. New master len {}'.format(datetime.now(), 
                                                                                len(tasks_df), 
                                                                                len(master_tasks_df)))

        print('{} ended list {}\n\n'.format(datetime.now(), spc['name']))

    print('parsed all spaces. Got {} task post pull date\n\n'.format(len(master_tasks_df)))

    standardize_column(master_tasks_df)

    master_tasks_df['pull_date'] = pull_date

    return master_tasks_df


# 
# ---------------------------------------------------------------
def standardize_column(df):

    columns = df.columns

    new_columns = [elm.replace('.','_') for elm in columns]

    df.columns = new_columns

    return df
    

# 
# ---------------------------------------------------------------
def get_clockify_clients_bq():
    df = pd.DataFrame()
    try:
        sql = "select *   from {}.{}.{}".format(
            bq.gcp_project, 
            bq.bq_dataset,
            db.CLOCKIFY_CLIENT
        )

        df = bq.gcp2df(sql) 

        return df
        value_list = df.values.tolist()
        print(value_list)

    except Exception as E:
        print(str(E))
    


# 
# ---------------------------------------------------------------
def create_clockify_task(proj_id, task_name, clickup_list_id, clickup_task_jd):
    try:
        # proj_id = '63e23e4c192143097fc8d3ea'
        url = api.clockify_task_api.format(project_id=proj_id)
        
        payload = json.dumps({
            "name": task_name
        })

        # payload = json.dumps({
        #     "name": task_name+str(datetime.now())
        # })

        response = requests.post(url, headers=api.clockify_header, data=payload)

        if response.status_code == 201:
            # log_error(clickup_task_jd+' - '+str(resp), 'feb_22_tasks_created')
            resp = response.json()
            resp['clickup_list_id'] = clickup_list_id
            resp['clickup_task_id'] = clickup_task_jd
            # resp['pull_date'] = current_date_time()

            return resp
        else:
            log_error(clickup_task_jd+' - '+task_name, 'feb_22_task_not_created')
            resp = response.json()
            return {}
            
    except Exception as e:
        log_error(clickup_task_jd + " <<clickup_task_jd. Err Message " + str(e), "tasks_not_created_on_clockify_run2")
        # print(str(e))


# 
# ---------------------------------------------------------------
def log_error(txt, file_name):
    with open(file_name+'.txt', 'a') as f:
        f.write('\n'+txt)


def read_json_log(file_name):
    with open(file_name, 'r') as f:
       data = json.load(f)
       return data

def write_json_log(err, file_name):
    with open(file_name) as f:
       log_data = json.load(f)
    
    log_data.append(err)

    # print(log_data)
    with open(file_name, 'w') as f:
        json.dump(log_data, f)

        # write_json_log({"name": "clickup task 2", "pull_date": "2023-01-24 00:00:00"}, 'df_csv.json')


# 
# ---------------------------------------------------------------
def get_clockify_tasks(project_id):
    ''' returns List of Dictionary '''
    url = api.clockify_task_api.format(project_id=project_id)+'?page-size=5000&is-active=true'

    response = requests.get(url, headers=api.clockify_header)

    if response.status_code == 200:
        resp = json.loads(response.text)

        return resp

# 
# ---------------------------------------------------------------
def get_unix_timestamp(_db_date, timedelay=0):
    '''
    Calculates unix timestamp of pull_date passed on. with delay (minutes) if provided
    Returns: unix timestamp
    '''

    last_cycle = datetime.strptime(_db_date, "%Y-%m-%d %H:%M:%S") - timedelta(minutes=timedelay)

    unix_time = int(time.mktime(last_cycle.timetuple()) * 1000)

    return unix_time

# 
# ---------------------------------------------------------------
def DELETE_ALL_CLOCKIFY_TASK():
    

    project = get_clockify_projects()

    for prj in project:
        try:
            project_id = prj['id']
            
            if project_id == '63e23e4c192143097fc8d3ea': continue

            tasks = get_clockify_tasks(project_id=project_id)

            log_error('\n\nDELETING {} TASKS FOR PROJECT {}'.format(len(tasks), project_id), 'delete_task')
            
            for elm in tasks:
                try:
                    task_id = elm['id']

                    delete_clockify_task(project_id=project_id, task_id=task_id)
                except Exception as e:
                    print(str(e))
                    log_error('\nerror thrown for PROJECT {} -- TASK {}. {}'.format(project_id, task_id, str(e)), 'delete_task')

            log_error('\n\n PROJECT CLEANED {}\n\n'.format(prj['name']), 'delete_task')
        except Exception as e:
            print(str(e))
            
# 
# ---------------------------------------------------------------
def delete_clockify_task(project_id, task_id):
    ''' returns void
    logs SUCCESS ID / DELETE FAILED
    '''
    url = api.delete_clociky_task.format(projectId=project_id, taskId=task_id)

    response = requests.delete(url=url, headers=api.clockify_header)

    if response.status_code == 200:
        log_error('SUCCESS. ID {}__{}'.format(project_id, task_id), 'delete_task')
    else:    
        log_error('\nDELETE FAILED. ID {}__{}'.format(project_id, task_id), 'delete_task')


# 
# ---------------------------------------------------------------
def update_task_name(project_id, task_id, clickup_parent_id, clickup_child_id, child_name, parent_name):
    try:
        url = api.delete_clociky_task.format(projectId=project_id, taskId=task_id)

        payload = json.dumps({
            "name": "{}-{}-{}-{}".format(clickup_child_id, child_name, clickup_parent_id, parent_name)
        })
        response = requests.put(url=url, headers=api.clockify_header , data=payload)

        if response.status_code == 200:
            log_error('updated {}.'.format(task_id), 'updated_clockify_tasks')
    except Exception as e:
        print(str(e))

def current_date_time():
    ''' String Format : '2023-02-23 19:23:44' '''
    try:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        print(str(e))


# 
# ---------------------------------------------------------------
def get_all_clockify_tasks():
    
    project = get_clockify_projects()
    
    master_df = pd.DataFrame()

    for prj in project:
        try:
            print('project '+prj['id']+prj['name'])
            project_id = prj['id']
            
            if project_id == '63e23e4c192143097fc8d3ea': continue

            tasks = get_clockify_tasks(project_id=project_id)

            task_df = pd.DataFrame(tasks)

            task_df['clickup_task_id'] = 'undefined'
            task_df['clickup_list_id'] = 'undefined'
            task_df['pull_date'] = current_date_time()

            # for idx, elm in task_df.iterrows():
            #     if elm['name'][7] == ':':
            #         task_df['clickup_task_id'][idx] = elm['name'].split(':')[0]
            #     if elm['name'][7] == '-':
            #         task_df['clickup_task_id'][idx] = elm['name'].split('-')[0]
            print('{} tasks added '.format(len(task_df)))
            master_df = pd.concat([master_df, task_df])
            # bq.df2gcp(task_df, 'clockify_tasks_2', mode='replace')

        except Exception as e:
            print(str(e))

    return master_df


## --------------------------------------------------------------------------------------------------
## GET LIST SPACES THAT NEEDS NOT TO BE MOVED TO CLOCKIFY
## --------------------------------------------------------------------------------------------------
def get_clickup_rejected_spaces():
    try:
        rejected_list = CONFIG.get('rejected_clickup_space_ids')
        rejected_list_str = [str(x) for x in rejected_list]
        return rejected_list_str        
        
    except Exception as e:
        print(str(e))


# 
# ---------------------------------------------------------------
def dump_new_clickup_list_to_bq(all_lists):
    try:
        if len(all_lists)>0:
            all_lists_df = pd.json_normalize(all_lists)

            if len(all_lists_df.columns)>0:

                new_df = standardize_column(all_lists_df)

                new_df['pull_date'] = current_date_time()

                bq.df2gcp(new_df, db.CLICKUP_LIST, mode='append')
            else:
                log_error('NO COLUMNS FOUND IN NEW LIST', 'log__'+str(date.today()))

    except Exception as e:
        log_error(str(e), 'log__'+str(date.today()))
        print(str(e))


# 
# ---------------------------------------------------------------
def dump_new_clockify_project_to_bq(_responses):
    '''Append new projects entries in clockify_projects table'''
    try:
        if len(_responses)>0:
            all_lists_df = pd.json_normalize(_responses)

            if len(all_lists_df.columns)>0:

                project_df = standardize_column(all_lists_df)
                project_df.drop(axis = 1, columns=['memberships'], inplace=True)
                project_df['pull_date'] = current_date_time()

                bq.df2gcp(project_df, db.CLOCKIFY_PROJECT, mode='append')
            else:
                log_error('NO COLUMNS FOUND IN NEW LIST', 'log__'+str(date.today()))

    except Exception as e:
        # log_error(str(e), 'log__'+str(date.today()))
        print(str(e))


# 
# ---------------------------------------------------------------
def dump_new_clickup_space_to_bq(_responses_df, drop_col=[]):
    '''Append new space entries in clickup_space table'''
    try:
        if len(_responses_df.columns)>0:

            space_df = standardize_column(_responses_df)
            # project_df.drop(axis = 1, columns=[], inplace=True)
            db_columns = ['id','name','color','private','admin_can_manage','multiple_assignees',
                          'archived','pull_date']
            space_df = space_df[db_columns]

            space_df['pull_date'] = current_date_time()

            bq.df2gcp(space_df, db.CLICKUP_SPACE, mode='replace')
        else:
            log_error('NO COLUMNS FOUND IN NEW LIST', 'log__'+str(date.today()))

    except Exception as e:
        # log_error(str(e), 'log__'+str(date.today()))
        print(str(e))
