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


def get_clickup_lists(space_id):
    try:
        lists = []
        url = api.clickup_folderless_list.format(space_id=space_id)

        response = requests.get(url, headers=api.clickup_header)

        if response.status_code == 200:
            resp = response.json()
            lists = resp.get('lists')
            #print(lists)
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
            folder_id = elm.get('id')
            url = api.clickup_list_with_folder.format(folder_id=folder_id)

            response = requests.get(url, headers=api.clickup_header)

            if response.status_code == 200:
                resp = response.json()
                lists_1 = resp.get('lists')
                #print(lists_1)
                lists.extend(lists_1)
                #print(lists)
                return lists
            else:
                return lists

    except Exception as e:
        print(str(e))


get_clickup_lists(19202777)