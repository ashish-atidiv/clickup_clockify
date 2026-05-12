import logging
import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger(__name__)

gcp_project = 'productivity-377410'
bq_dataset = 'tickets_dataset'

client = bigquery.Client(project=gcp_project)


def gcp2df(sql):
    query = client.query(sql)
    results = query.result()
    return results.to_dataframe()


def df2gcp(dataframe, table_name, mode='append'):
    table_id = f"{gcp_project}.{bq_dataset}.{table_name}"
    write_disposition = (
        bigquery.WriteDisposition.WRITE_TRUNCATE if mode == 'replace'
        else bigquery.WriteDisposition.WRITE_APPEND
    )
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    job = client.load_table_from_dataframe(dataframe, table_id, job_config=job_config)
    job.result()
    logger.info("BQ load complete: %d rows → %s", len(dataframe), table_id)
