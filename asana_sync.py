from utils.utils import *
import utils.asana_utils as asana
from utils.bigquery_utils import *
import utils.db as db

pull_date = current_date_time()

def asana_data_pull():
    try: 

        pull_date = current_date_time() 

        '''
        # clockify_client_df = create_clockify_client(asana.asana_workspace_name, client_note=asana.asana_workspace_id)
        # clockify_client_df['clickup_space_id'] = asana.asana_workspace_id
        # clockify_client_df['pull_date'] = pull_date

        # ## Insert data in database
        # if len(clockify_client_df) > 0:
        #     df2gcp(df, db.CLOCKIFY_CLIENT, mode='append')
        '''
        
        # # Create PROJECTS on clockify for LISTS which do not exist on clockify
        # #------------------------------------------------------------------------ 
        # import ipdb; ipdb.set_trace()

        clockify_projects = get_clockify_projects()
        list_ids_in_projects = [x['note'] for x in clockify_projects]

        asana_projects = asana.get_projects()

        for elm in asana_projects:
            
            try:
                if elm['gid'] not in list_ids_in_projects:
                    projectName = elm.get('name')
                    project_id = elm.get('gid')
                    workspace_id = '63f7670ea561315041e6cd07'

                    resp = create_clockify_projects(project_name=projectName, project_note = project_id, client_id= workspace_id )
                else:
                    print('Existing Project ', elm.get('name'))

            except Exception as e:
                print(str(e))
            


        ## --------------------------------------------------------------------------------
        # GET ASANA TASK

        master_df = pd.DataFrame()

        for elm in asana_projects:
            try:

                project_id = elm['gid']

                tasks_list = asana.get_tasks(project_id)

                df_task = pd.DataFrame(tasks_list)
                df_task['project_id'] = project_id
                df_task['pull_date'] = pull_date

                master_df = pd.concat([master_df, df_task])

            except Exception as e:
                print(str(e))
            
        df2gcp(master_df, db.ASANA_TASKS, mode='replace')


        # CREATE CLOCKIFY TASK
        ## --------------------------------------------------------------------------------
        try:
            # this is a double check being set to make sure we dont create a task twice on clockify  
            clockify_bq_task = gcp2df("select distinct clickup_task_id \
                from `productivity-377410.tickets_dataset.clockify_task`").values.tolist()
            clockify_bq_task_list = [x[0] for x in clockify_bq_task]
        except Exception as e: print(str(e))

        # Remove existing tasks
        master_df = master_df[~master_df.gid.isin(clockify_bq_task_list)]

        # # # ## ------------------------------------------------------------------------------

        clockify_projects = get_clockify_projects()
        clockify_projects_lst = [
            {"platform_project_id": x['note'], "clockify_project_id": x['id'], "clockify_project_name": x['name']} for x in clockify_projects
        ]
        project_df = pd.DataFrame(clockify_projects_lst)
        
        # # # ## ------------------------------------------------------------------------------

        import ipdb; ipdb.set_trace()
        new_task_created = []
        pull_date = current_date_time()

        for idx, elm in master_df.iterrows():
            try:
                # Get Project id against List 
                clk_project_id = project_df[ project_df.platform_project_id == elm['project_id']]['clockify_project_id'].values

                if len(clk_project_id):
                    clk_project_id = project_df[ project_df.platform_project_id == elm['project_id']]['clockify_project_id'].values[0]
                    clk_project_name = project_df[ project_df.platform_project_id == elm['project_id']]['clockify_project_name'].values[0]

                    if clk_project_id and elm['gid'] not in clockify_bq_task_list:
                        to_name = elm['gid']+' || '+elm['name']
                        resps = create_clockify_task(clk_project_id, to_name, elm['project_id'], elm['gid'])        
                        if resps:
                            print('Task created '+elm['gid'])
                            new_task_created.append(resps)
                        else:
                            print(elm['name']+' ---NOT created')
                # else:
                    # print(elm)
            except Exception as e: print(str(e)+ ' ' + elm['list_name'])


        import ipdb; ipdb.set_trace()
        df_to_write = pd.DataFrame(new_task_created)
        print('{} records to write to clockify_task '.format( len(df_to_write) ))
        
        # # update succesfull task to BQ
        if len(df_to_write):
            bq.df2gcp(df_to_write, db.CLOCKIFY_TASK, mode='append')
        
  
    
    except Exception as e: 
        print(str(e))
    

# main()