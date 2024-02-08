
'''
This file contains all the endpoints used in this project
'''

import settings as env

#---------------------------------------------------------------------------------------
#  CLICK UP
#---------------------------------------------------------------------------------------
clickup_team_id = env.clickup_team_id
clickup_base_url = 'https://api.clickup.com/api/v2'

clickup_spaces = clickup_base_url+"/team/"+clickup_team_id+"/space"

clickup_folders = clickup_base_url+"/space/{space_id}/folder"

clickup_list_with_folder = clickup_base_url+"/folder/{folder_id}/list"  
clickup_folderless_list = clickup_base_url+"/space/{space_id}/list"

clickup_header = {"Authorization": env.clickup_token, "Content-Type": "application/json"}

clickup_task = clickup_base_url+"/list/{list_id}/task?page={page_no}&subtasks=true&include_closed=true&date_created_gt={date_created_gt}"

#---------------------------------------------------------------------------------------
#  CLOCKIFY
#---------------------------------------------------------------------------------------
clockify_base_url = 'https://api.clockify.me/api/v1'
clockify_workspace_id = env.clockify_workspace_id

clockify_client_api = clockify_base_url+"/workspaces/"+clockify_workspace_id+"/clients"

clockify_project_api = clockify_base_url+"/workspaces/"+clockify_workspace_id+"/projects"

clockify_task_api = clockify_base_url+"/workspaces/"+clockify_workspace_id+"/projects/{project_id}/tasks"

clockify_header = {"X-Api-Key": env.clocify_token, "Content-Type": "application/json"}
clockify_params = {"page-size": 1000}
delete_clociky_task = clockify_base_url+"/workspaces/"+clockify_workspace_id+"/projects/{projectId}/tasks/{taskId}"

# /workspaces/{workspaceId}/projects/{projectId}/tasks/{taskId}


#---------------------------------------------------------------------------------------
#  ASANA
#---------------------------------------------------------------------------------------

asana_base_url = 'https://app.asana.com/api/1.0'

asana_header = {"Authorization": "Bearer "+env.asana_token, "Accept": "application/json"}

asana_project_api = asana_base_url+'/projects?workspace=701236911131463&archived=false&opt_pretty=true'

asana_task_api = asana_base_url+'/tasks?project={}&opt_pretty=true'