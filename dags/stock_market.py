from airflow.decorators import dag, task
from airflow.hooks.base import BaseHook
from airflow.sensors.base import PokeReturnValue
from airflow.operators.python import PythonOperator
from airflow.providers.docker.operators.docker import DockerOperator
from astro import sql as aql
from astro.files import File
from astro.sql.table import Table, Metadata
from datetime import datetime

from include.stock_market.tasks import _get_stock_prices, _store_stock_prices, _get_formatted_csv, BUCKET_NAME

SYMBOL = 'NVDA'

@dag(
    start_date=datetime(2023, 1, 1),
    schedule='@daily',
    catchup=False,
    tags=['stock_market']
    # on_success_callback=SlackNotifier(
    #     slack_conn_id='slack',
    #     text='the Dag stock_market has succedd',
    #     channel='general'
    # ),
    # on_failure_callback=SlackNotifier(
    #     slack_conn_id='slack',
    #     text='the Dag stock_market has failed',
    #     channel='general'
    # ),

)
def stock_market():
    #30seconds se ejecutara
    @task.sensor(poke_interval=30, timeout=300, mode='poke')
    def is_api_available() -> PokeReturnValue:
        #reqeust para hacer la peticion http
        import requests
        #remember that we define this in the Admin>Connection section
        api = BaseHook.get_connection('stock_api')
        url = f"{api.host}{api.extra_dejson['endpoint']}"
        print(url)
        #pasar la url y los headers de este 
        response = requests.get(url, headers=api.extra_dejson['headers'])
        #logica por si no trae resultados 
        condition = response.json()['finance']['result'] is None
        #retorna la respuesta y compartir el valor con la siguiente tarea xcom_value
        return PokeReturnValue(is_done=condition, xcom_value=url)
    
    get_stock_prices = PythonOperator(
        task_id='get_stock_prices',
        python_callable=_get_stock_prices,
        op_kwargs={'url': '{{ ti.xcom_pull(task_ids="is_api_available") }}', 'symbol': SYMBOL}
    )

    store_stock_prices = PythonOperator(
        task_id='store_stock_prices',
        python_callable=_store_stock_prices,
        op_kwargs={'stock': '{{ti.xcom_pull(task_ids="get_stock_prices") }}'}
    )

    format_prices = DockerOperator(
        task_id='format_prices',
        image='airflow/spark-stock-app',
        container_name='format_prices',
        api_version='auto',
        auto_remove='success',#delete contianer as soon the task is completed
        docker_url='tcp://docker-proxy:2375',
        network_mode='container:spark-master',#same network as the one spark master contianer is using
        tty=True,
        xcom_all=False,
        mount_tmp_dir=False,
        environment={
            'SPARK_APPLICATION_ARGS': '{{ti.xcom_pull(task_ids="store_stock_prices")}}'
        }
    )

    get_formatted_csv = PythonOperator(
        task_id='get_formatted_csv',
        python_callable=_get_formatted_csv,
        op_kwargs={
            'path': '{{ti.xcom_pull(task_ids="store_stock_prices") }}'
            }
    )

    load_to_dw = aql.load_file(
        task_id='load_to_dw',
        input_file=File(
            path=f"s3://{BUCKET_NAME}/{{{{ ti.xcom_pull(task_ids='get_formatted_csv') }}}}",
            conn_id='minio'
        ),
        output_table=Table(
            name='stock_market',
            conn_id='postgres',
            metadata=Metadata(
                schema='public'
            )
        ),
        load_options={
            "aws_access_key_id": BaseHook.get_connection('minio').login,
            "aws_secret_access_key": BaseHook.get_connection('minio').password,
            "endpoint_url": BaseHook.get_connection('minio').host,
        }
    )
    
    is_api_available() >> get_stock_prices >> store_stock_prices >> format_prices >> get_formatted_csv >> load_to_dw
        

stock_market()
