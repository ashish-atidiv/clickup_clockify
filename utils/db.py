from envyaml import EnvYAML

devMode = EnvYAML('config.yaml').get('dev_mode')
table_prefix = '_' if devMode else ''

CLICKUP_SPACE = table_prefix + 'clickup_space'
CLICKUP_TASK = table_prefix + 'clickup_task'
CLICKUP_LIST = table_prefix + 'clickup_list'

CLOCKIFY_CLIENT = table_prefix + 'clockify_client'
CLOCKIFY_TASK = table_prefix + 'clockify_task'

CLOCKIFY_PROJECT = table_prefix + 'clockify_project'
