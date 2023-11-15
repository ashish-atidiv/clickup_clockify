from utils.utils import *
from utils.bigquery_utils import *
import utils.db as db
from asana_sync import asana_data_pull

pull_date = current_date_time()


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
    
    ## Insert data in database
    if len(spaces) > 0:
        dump_new_clickup_space_to_bq(spaces)
        # df2gcp(spaces, db.CLICKUP_SPACE, mode='replace')

    if len(new_client_to_write_to_db) > 0:
        df2gcp(new_client_to_write_to_db, db.CLOCKIFY_CLIENT, mode='append')

    print('{} new spaces dumpedd. Passing forward {} space in total'.format(len(new_client_to_write_to_db), len(all_spaces)))
    return all_spaces


def clickup_list(all_spaces):
    # # Create PROJECTS on clockify for LISTS which do not exist on clockify
    # #------------------------------------------------------------------------ 
    # all_spaces = gcp2df("select clickup_space_id as id, name from `{}.{}.{}`".format(bq.gcp_project, bq.bq_dataset, db.CLOCKIFY_CLIENT))
    # clockify_clients = get_clockify_clients() ## Redundant function, output not being used anywhere
    space_client_mapping = get_space_client_mapping()

    clockify_projects = get_clockify_projects()
    list_ids_in_projects = [x['note'] for x in clockify_projects]

    # new_projects_to_write_to_db = pd.DataFrame()
    rejected_clickup_list = get_clickup_rejected_spaces()
    all_lists = []
    success_response_list = []
    for idx, spc in all_spaces.iterrows():
        clickup_list = get_clickup_lists(spc['id']) if spc['id'] not in rejected_clickup_list else []
        # all_lists.extend(clickup_list) # onetime load
        if clickup_list:
            for elm in clickup_list:
                try:
                    if elm['id'] not in list_ids_in_projects: ## If this condition is true, project exists 
                        list_name = elm.get('name')
                        list_id = elm.get('id')
                        list_space_id = space_client_mapping[spc['id']]

                        resp, json_response = create_clockify_projects(list_name, project_note = list_id, client_id= list_space_id )
                        if resp == 201:
                            all_lists.append(elm) ## to be used on incremental load
                            success_response_list.append(json_response)
                            clockify_projects.append(json_response)
                        # resp['clickup_space_id'] = list_space_id
                        # resp['clickup_list_id'] = list_id
                        # resp['pull_date'] = pull_date

                        # temp_df = pd.DataFrame([resp])
                        # new_projects_to_write_to_db = pd.concat([new_projects_to_write_to_db, temp_df])

                except Exception as e:
                    print(str(e))
        
        # if len(clickup_list) == 0: print('NO LIST FETCHED FOR PROJECT {}-{}'.format(spc['id'], spc['name']))
    
    ## Prepare Clickup List Dump
    dump_new_clickup_list_to_bq(all_lists)
    
    print('New projects to dump {}'.format(len(success_response_list)))
    dump_new_clockify_project_to_bq(success_response_list)

    return clockify_projects


def clickup_tasks(_all_clockify_projects, clickup_task_df):

    ''' GETTING ALL CLIKCUP TASKS IN DATAFRAME and UPLOAD TO BIGQUERY '''
    # ----------------------------------------------------------------------------------- '''

    clickup_task_df = clickup_task_df[ clickup_task_df.id != '12ck3ph']

    clickup_task_df.drop(axis = 1, columns = ['custom_fields'], inplace = True)
    
    # df2gcp(clickup_task_df, db.CLICKUP_TASK, mode = 'append')


    ''' CREATE TASKS ON CLOCKIFY FROM BIGQUERY '''

    # date_df = gcp2df('select max(pull_date) as pull_date from `productivity-377410.tickets_dataset.clickup_task`')
    # max_datetime = str(date_df.values[0][0])
    # ## ------------------------------------------------------------------------------

    # clickup_df = gcp2df("select id , name, list_id , list_name \
    #      from `{}.{}.{}`".format(bq.gcp_project, bq.bq_dataset, db.CLICKUP_TASK))
    clickup_df = clickup_task_df[['id' , 'name', 'list_id' , 'list_name']]
    # clickup_df = clickup_task_df ## both are same are we need the latest data from clickup

    # # ## ------------------------------------------------------------------------------

    # clockify_projects = get_clockify_projects()
    clockify_projects = _all_clockify_projects
    clockify_projects_lst = [
        {"clickup_id": x['note'], "clockify_project_id": x['id'], "clockify_project_name": x['name']} for x in clockify_projects
    ]
    project_df = pd.DataFrame(clockify_projects_lst)
    
    # db_project = pd.DataFrame(clockify_projects)
    # db_project.drop(axis = 1, columns = ['memberships'], inplace=True)
    # db_project['pull_date'] = current_date_time()

    # df2gcp(db_project, db.CLOCKIFY_PROJECT, mode = 'replace')

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
    
    df2gcp(df_for_db, db.CLICKUP_TASK, mode = 'append')

    # -----------------------------------------------------------------------------------
    # CREATE NEW TASK ON CLOCKIFY

    new_task_created = []

    for idx, elm in clickup_trimmed_df.iterrows():
        try:
            # Get Project id against List 
            clk_project_id = project_df[ project_df.clickup_id == elm['list_id']]['clockify_project_id'].values

            if len(clk_project_id):
                clk_project_id = project_df[ project_df.clickup_id == elm['list_id']]['clockify_project_id'].values[0]
                clk_project_name = project_df[ project_df.clickup_id == elm['list_id']]['clockify_project_name'].values[0]

                if clk_project_id and elm['id'] not in clockify_bq_task_list:
                    # print('new tak ' + elm['name'])
                    resps = create_clockify_task(clk_project_id, elm['id']+' || '+elm['name'], elm['list_id'], elm['id'])        
                    if resps:
                        new_task_created.append(resps)
                    else:
                        print(elm['name']+' ---NOT created')
            # else:
                # print(elm)
        except Exception as e: print(str(e)+ ' ' + elm['list_name'])


    df_to_write = pd.DataFrame(new_task_created)
    df_to_write['pull_date'] = pull_date
    print('{} records to write to clockify_task and {} new tasks were found in clickup '.format(len(df_to_write), len(clickup_trimmed_df) ))
    print(df_to_write.head())
    # update succesfull task to BQ
    bq.df2gcp(df_to_write, db.CLOCKIFY_TASK, mode='append')

def main():
    print(datetime.now())
    
    clients = clickup_spaces()
  
    projects = clickup_list(clients)
    # import ipdb; ipdb.set_trace()
    
    clickup_task_df = fetch_all_clickup_tasks()
    
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

main()