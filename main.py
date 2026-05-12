from utils.utils import *
from utils.bigquery_utils import *
import utils.db as db
from asana_sync import asana_data_pull
import os
import json
import argparse
pull_date = current_date_time()
# credential_path = "./productivity.json"
credential_path = "C:\\Users\\Ashish Agrawal\\Documents\\Codes\\codebase\\gcloud\\productivity.json"

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credential_path

def parse_arguments():
    """
    Parse command line arguments for configurable incremental loading.

    Returns:
        argparse.Namespace: Parsed arguments with lookback_days and buffer_seconds
    """
    parser = argparse.ArgumentParser(
        description='''ClickUp to Clockify Data Sync with Incremental Loading

Syncs ClickUp Spaces/Lists/Tasks to Clockify Clients/Projects/Tasks and stores data in BigQuery.

Incremental loading allows you to control how much historical data is fetched:
- First run: Uses --lookback-days to fetch tasks from N days ago
- Subsequent runs: Uses --buffer-seconds to fetch tasks with a time buffer from last sync

For detailed usage guide, see USAGE.md''',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  python main.py                        Use defaults from config.yaml (1 day lookback, 4 hour buffer)
  python main.py --lookback-days 7      Fetch tasks from last 7 days (first run only)
  python main.py --buffer-seconds 3600  Use 1 hour buffer on subsequent runs
  python main.py -d 30 -b 7200          30 day lookback, 2 hour buffer

Configuration Priority:
  1. Command line arguments (highest)
  2. config.yaml settings
  3. Hardcoded defaults (1 day, 14400 seconds)

For more information, see USAGE.md'''
    )

    parser.add_argument(
        '--lookback-days',
        '-d',
        type=int,
        default=None,
        metavar='DAYS',
        help='''Number of days to look back for incremental loading on FIRST RUN.
Used when no previous pull_date exists in BigQuery.
Calculates: fetch_from = now() - DAYS.
Overrides config.yaml setting. (Default from config: 1)'''
    )

    parser.add_argument(
        '--buffer-seconds',
        '-b',
        type=int,
        default=None,
        metavar='SECONDS',
        help='''Buffer time in seconds for SUBSEQUENT RUNS.
Subtracts SECONDS from last pull_date to catch late updates.
Helps handle timezone issues and delayed task creation.
Overrides config.yaml setting. (Default from config: 14400 = 4 hours)
Common values: 3600 (1h), 7200 (2h), 14400 (4h), 28800 (8h)'''
    )

    return parser.parse_args()

def clickup_spaces():

    # CREATE NEW CLIENTS ON CLOCKIFY FROM CLICKUP SPACES
    # --------------------------------------------------- 
    spaces_data = get_clickup_spaces()
    all_spaces = pd.DataFrame(spaces_data)
    
    # clockify_clients = get_clockify_clients()
    clockify_clients = get_clockify_clients_bq()

    # space_ids_in_clients = [x['note'] for x in clockify_clients] ## this one works when we fetch clikcup id from clockify client notes
    # space_ids_in_clients = [x[7] for x in clockify_clients]
    space_ids_in_clients = clockify_clients['clickup_space_id'].values.tolist()
    
    spaces = all_spaces[~all_spaces['id'].isin(space_ids_in_clients)]

    spaces.to_csv("Spaces.csv", index= False)
    new_client_to_write_to_db = pd.DataFrame()

    # Create CLIENTS for those SPACES from clickup which do not exist on clockify

    for idx, elm in spaces.iterrows():
        try:
            if elm['id'] not in space_ids_in_clients: 
                client_name = elm.get('name') 
                space_id = elm.get('id')
                
                clockify_client_df = create_clockify_client(client_name, client_note= space_id)
                clockify_client_df['clickup_space_id'] = space_id
                clockify_client_df['pull_date'] = pull_date

                new_client_to_write_to_db = pd.concat([new_client_to_write_to_db, clockify_client_df])

            else:
                print('client existed ', elm['name'])
            
        except Exception as err:
            print(str(err))
    
    all_spaces['pull_date']= pull_date
    all_spaces['archived']= all_spaces['archived'].astype("string")
    
    df2gcp(all_spaces, db.CLICKUP_SPACE, mode='replace')
    ## Insert data in database
    if len(spaces) > 0:
        dump_new_clickup_space_to_bq(spaces)
        # df2gcp(spaces, db.CLICKUP_SPACE, mode='replace')

    if len(new_client_to_write_to_db) > 0:
        df2gcp(new_client_to_write_to_db, db.CLOCKIFY_CLIENT, mode='append')
        # new_client_to_write_to_db("./data/clockify_client.csv", index=False)

    print('{} new spaces dumpedd. Passing forward {} space in total'.format(len(new_client_to_write_to_db), len(all_spaces)))
    return all_spaces


def clickup_list(all_spaces):
    # # Create PROJECTS on clockify for LISTS which do not exist on clockify
    # #------------------------------------------------------------------------ 
    # all_spaces = gcp2df("select clickup_space_id as id, name from `{}.{}.{}`".format(bq.gcp_project, bq.bq_dataset, db.CLOCKIFY_CLIENT))
    # clockify_clients = get_clockify_clients() ## Redundant function, output not being used anywhere
    space_client_mapping = get_space_client_mapping()
        
    #import ipdb; ipdb.set_trace()
    clockify_projects = get_clockify_projects()
    
    # with open("./data/new_clock_pros.json", "w") as file:
    #     file.write(json.dumps(clockify_projects))
        
    # print(clockify_projects)
    list_ids_in_projects = [x['note'] for x in clockify_projects]
    # print("list_ids_in_projects",list_ids_in_projects)

    # new_projects_to_write_to_db = pd.DataFrame()
    rejected_clickup_list = get_clickup_rejected_spaces()
    
    # with ("rejected_lists.txt", 'w') as file:
    #     file.write(str(rejected_clickup_list))
    # print("rejected_clickup_list",rejected_clickup_list)
    
    all_lists = []
    success_response_list = []
    for idx, spc in all_spaces.iterrows():
        clickup_list = get_clickup_lists(spc['id']) if spc['id'] not in rejected_clickup_list else []
        
        # with open("clickup_list.txt", "a") as file:
        #     file.write(str(clickup_list))
        
        # cli_list= pd.DataFrame(clickup_list)
        # cli_list.to_csv("click_up_lists.csv", index=False)
        
        # all_lists.extend(clickup_list) # onetime load
        # print("space ",spc['id'], "clickup list  ", clickup_list )
        if clickup_list:
            # print("\n\n\n\n\n\n\n\n CLICKUP LIST HERE ..................................................................")
            # print(clickup_list)
            # print("\n\n\n\n\n\n\n\n LIST ID PROJECTS HERE ..................................................................")
            # print(list_ids_in_projects)
            for elm in clickup_list:
                try:
                    # print(elm['id'])
                    if elm['id'] not in list_ids_in_projects: ## If this condition is true, project exists 
                        list_name = elm.get('name')
                        # print(list_name)
                        list_id = elm.get('id')
                        # print(list_id)
                        list_space_id = space_client_mapping[spc['id']]
                        # print(list_space_id)
                        #import ipdb; ipdb.set_trace()
                        print(create_clockify_projects(list_name, project_note = list_id, client_id= list_space_id ))
                        resp, json_response = create_clockify_projects(list_name, project_note = list_id, client_id= list_space_id )

                        if resp == 201:
                        # with open("all_lists.txt", "a") as file:
                        #     file.write(str(all_lists))
                            success_response_list.append(json_response)
                            clockify_projects.append(json_response)
                        # resp['clickup_space_id'] = list_space_id
                        # resp['clickup_list_id'] = list_id
                        # resp['pull_date'] = pull_date

                        # temp_df = pd.DataFrame([resp])
                        # new_projects_to_write_to_db = pd.concat([new_projects_to_write_to_db, temp_df])
                    all_lists.append(elm) ## to be used on incremental load

                except Exception as e:
                    print(str(e))
                
        # print("\n\n\n\n\n\n\n\n ALL LIST HERE ..................................................................")
        # print(all_lists)
        # print("all list ends here")
        
        # if len(clickup_list) == 0: print('NO LIST FETCHED FOR PROJECT {}-{}'.format(spc['id'], spc['name']))
    
    ## Prepare Clickup List Dump
    dump_new_clickup_list_to_bq(all_lists)
    
    # print('New projects to dump {}'.format(len(success_response_list)))
    # dump_new_clockify_project_to_bq(success_response_list)

    return clockify_projects


def clickup_tasks(_all_clockify_projects, clickup_task_df):

    ''' GETTING ALL CLIKCUP TASKS IN DATAFRAME and UPLOAD TO BIGQUERY '''
    # ----------------------------------------------------------------------------------- '''

    clickup_task_df = clickup_task_df[ clickup_task_df.id != '12ck3ph']

    # Drop columns that cause schema issues or are unused
    # - custom_fields: Complex nested structure, not needed
    # - group_assignees: Schema expects INTEGER array but may receive UUIDs
    # - dependencies: Contains chain_id field that changed from INTEGER to UUID
    columns_to_drop = ['custom_fields', 'dependencies']
    if 'group_assignees' in clickup_task_df.columns:
        columns_to_drop.append('group_assignees')
    clickup_task_df.drop(axis=1, columns=columns_to_drop, inplace=True, errors='ignore')

    # clickup_task_df.to_csv("data.csv", index=False)

    ################ FOR FULL REFRESH UNCOMMENT THE DF2GCP WITH REPLACE AND SET THE DB_PULL_DATE IN UTILS.PY LINE NUMBER 255
    # df2gcp(clickup_task_df, db.CLICKUP_TASK, mode = 'replace')
    clickup_task_df['start_date']=pd.to_numeric(clickup_task_df['start_date'], errors='coerce').astype('Int64')
    clickup_task_df['due_date']=clickup_task_df['due_date'].astype('string')

    df2gcp(clickup_task_df, db.CLICKUP_TASK, mode = 'append')
    


    ''' CREATE TASKS ON CLOCKIFY FROM BIGQUERY '''

    # date_df = gcp2df('select max(pull_date) as pull_date from `productivity-377410.tickets_dataset.clickup_task`')
    # max_datetime = str(date_df.values[0][0])
    # ## ------------------------------------------------------------------------------

    # clickup_df = gcp2df("select id , name, list_id , list_name \
    #      from `{}.{}.{}`".format(bq.gcp_project, bq.bq_dataset, db.CLICKUP_TASK))
    clickup_df = clickup_task_df[['id' , 'name', 'list_id' , 'list_name']]
    # clickup_df.to_csv("./data/click_up_all_tasks.csv", index= False)
    # clickup_df = clickup_task_df ## both are same are we need the latest data from clickup

    # # ## ------------------------------------------------------------------------------

    # clockify_projects = get_clockify_projects()
    clockify_projects = _all_clockify_projects
    clockify_projects_lst = [
        {"clickup_id": x['note'], "clockify_project_id": x['id'], "clockify_project_name": x['name']} for x in clockify_projects
    ]
    project_df = pd.DataFrame(clockify_projects_lst)
    
    
    ############### This part was commented #######################################
    db_project = pd.DataFrame(clockify_projects)
    db_project.drop(axis = 1, columns = ['memberships'], inplace=True)
    db_project['pull_date'] = current_date_time()

    df2gcp(db_project, db.CLOCKIFY_PROJECT, mode = 'replace')

    #####################################################################################
    # # ## ------------------------------------------------------------------------------

    try:
        # this is a double check being set to make sure we dont create a task twice on clockify  
        clockify_bq_task_list = []
        clockify_bq_task = gcp2df("select distinct clickup_task_id \
            from `{}.{}.{}`".format(bq.gcp_project, bq.bq_dataset, db.CLOCKIFY_TASK)).values.tolist()
        clockify_bq_task_list = [x[0] for x in clockify_bq_task]
    except Exception as e: print(str(e))

    # # ## ------------------------------------------------------------------------------

    # Remove ids of tasks which have been created 
    clickup_trimmed_df = clickup_df[~clickup_df['id'].isin(clockify_bq_task_list)].reset_index()
    df_for_db = clickup_df[~clickup_df['id'].isin(clockify_bq_task_list)]

    print('{} tasks to be created '.format(len(clickup_trimmed_df)))
    
    # df2gcp(df_for_db, db.CLICKUP_TASK, mode = 'replace')
    df_for_db.to_csv("./data/clickup_task.csv", index= False)

    # -----------------------------------------------------------------------------------
    # CREATE NEW TASK ON CLOCKIFY

    new_task_created = []
    new_task_list= []
    for idx, elm in clickup_trimmed_df.iterrows():
        try:
            # Get Project id against List 
            clk_project_id = project_df[ project_df.clickup_id == elm['list_id']]['clockify_project_id'].values

            if len(clk_project_id):
                clk_project_id = project_df[ project_df.clickup_id == elm['list_id']]['clockify_project_id'].values[0]
                clk_project_name = project_df[ project_df.clickup_id == elm['list_id']]['clockify_project_name'].values[0]
                
                if clk_project_id and elm['id'] not in clockify_bq_task_list:
                    print('new task here!' + elm['name'])
                    new_task_list.append(elm['id'])
                    resps = create_clockify_task(clk_project_id, elm['id']+' || '+elm['name'], elm['list_id'], elm['id'])        
                    if resps:
                        new_task_created.append(resps)
                    else:
                        print(elm['name']+' ---NOT created')
            # else:
                # print(elm)
        except Exception as e: print(str(e)+ ' ' + elm['list_name'])
        
    # with open("new_task_list.txt", "w") as file:
    #     file.write(str(new_task_list))

    df_to_write = pd.DataFrame(new_task_created)
    df_to_write['pull_date'] = pull_date
    # with open("data/new_task.txt", "w") as file:
    #     file.write(str(new_task_created))
    print('{} records to write to clockify_task and {} new tasks were found in clickup '.format(len(df_to_write), len(clickup_trimmed_df) ))
    # print(df_to_write.head())
    # print(df_to_write.columns)
    # df_to_write.to_csv("./data/new_task.csv", index=False)
    # clickup_trimmed_df.to_csv("./data/clockify_tasks.csv", index=False)
    # update succesfull task to BQ
    # bq.df2gcp(df_to_write, db.CLOCKIFY_TASK, mode='append')
    # bq.df2gcp(clickup_trimmed_df, db.CLOCKIFY_TASK, mode= 'replace')
    

# def upload_projects(projects):
#     df= pd.DataFrame(projects)
#     # df.to_csv('projects.csv', index=False)
#     df['pull_date'] = pull_date
    


def main(lookback_days=None, buffer_seconds=None):
    """
    Main execution function for ClickUp to Clockify sync.

    Args:
        lookback_days (int, optional): Days to look back for incremental loading
        buffer_seconds (int, optional): Buffer time in seconds
    """
    print(datetime.now())

    clients = clickup_spaces()

    projects = clickup_list(clients)

    # upload_projects(projects)
    # import ipdb; ipdb.set_trace()
    # with open("projects.json", 'w') as file:
    #     file.write(json.dumps(projects))
    clickup_task_df = fetch_all_clickup_tasks(
        lookback_days=lookback_days,
        buffer_seconds=buffer_seconds
    )
    
    # clickup_task_df.to_csv("click_up_tasks.csv", index=False)
    # print("CLICKUP TASK DF HERE")
    # print(clickup_task_df)
    ## Create tasks 
    if len(clickup_task_df):
        clickup_tasks(projects, clickup_task_df)
    
    ''' UPDATE CHILD TASKS '''
    
    # child_df = gcp2df(" select tbl1.id as child_id, tbl1.parent as parent_id, tbl1.name as child_name, tbl2.name as parent_name \
    #                     from `productivity-377410.tickets_dataset.clickup_task` tbl1 \
    #                     left join `productivity-377410.tickets_dataset.clickup_task` tbl2 \
    #                     on tbl1.parent = tbl2.id \
    #                     where tbl1.parent is not null ")

    # # ## ------------------------------------------------------------------------------

    # clockify_projects = get_clockify_projects()
    # clockify_projects_lst = [
    #     {"clickup_id": x['note'], "clockify_project_id": x['id'], "clockify_project_name": x['name']} for x in clockify_projects
    # ]
    # project_df = pd.DataFrame(clockify_projects_lst)

    # # ## ------------------------------------------------------------------------------

    # # try:
    # #     # this is a double check being set to make sure we dont create a task twice on clockify  
    # #     clockify_bq_task_list = []
    # #     clockify_bq_task = gcp2df("select distinct clickup_task_id \
    # #         from `productivity-377410.tickets_dataset.clockify_task`").values.tolist()
    # #     clockify_bq_task_list = [x[0] for x in clockify_bq_task]
    # # except Exception as e: print(str(e))

    # # ## ------------------------------------------------------------------------------
    
    
    # data = read_json_log('df_csv.json')

    # df = pd.DataFrame(data)
    # # clockify_task_ids = df['id'].values.tolist()


    # for idx, elm in child_df.iterrows():
    #     # print(elm['id'])
    #     sub_df = df[ df.clickup_task_id == elm['child_id']]['id'].reset_index()

    #     # print(sub_df)
    #     # print(len(sub_df))
    #     if len(sub_df):
    #         # print(sub_df['id'][0])  #projectId
    #         task_id = sub_df['id'][0]
    #         project_id = df[ df.clickup_task_id == elm['child_id']]['projectId'].reset_index()
    #         project_id = project_id['projectId'][0]
    #         update_task_name(project_id, task_id, elm['parent_id'], elm['child_id'], elm['child_name'], elm['parent_name'])

    
    # for idx, elm in child_df.iterrows():
        
    #     clk_project_id = project_df[ project_df.clickup_id == elm['clickup_list_id']]['clockify_project_id'].values[0]
    #     clk_project_name = project_df[ project_df.clickup_id == elm['clickup_list_id']]['clockify_project_name'].values[0]
        
    #     if clk_project_id: #and elm['clickup_task_id'] not in clockify_bq_task_list:
    #         print('CUP List {} <<>> {} CKFY Project. '.format(elm['clickup_list_name'], clk_project_name))


    #         create_clockify_task(clk_project_id, elm['clickup_task_id']+': '+elm['clikcup_task_name'], elm['clickup_list_id'], elm['clickup_task_id'])        
    
    # print('\n\nCREATED ALL TASKS FOR ALL PROJECTS \n\n')

    # print('\n\n HOLDING COMMNAD LINE \n\n')


    ''' ASANA SYNC '''
    #asana_data_pull()

if __name__ == '__main__':
    args = parse_arguments()
    main(lookback_days=args.lookback_days, buffer_seconds=args.buffer_seconds)
else:
    # Support programmatic invocation without arguments for backward compatibility
    main()