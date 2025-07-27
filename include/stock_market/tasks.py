from airflow.hooks.base import BaseHook
import json
from minio import Minio
from io import BytesIO
from airflow.exceptions import AirflowNotFoundException

BUCKET_NAME = 'stock-market'

def _get_minio_client():
    minio = BaseHook.get_connection('minio')
    client = Minio(
        endpoint=minio.extra_dejson['endpoint_url'].split('//')[1],
        access_key=minio.login,#AWS aceess key
        secret_key=minio.password,#AWS secret access key
        secure=False
    )
    return client

#fetch data from the url
def _get_stock_prices(url, symbol):
    import requests
    import json
    
    url = f"{url}{symbol}?metrics=high?&interval=1d&range=1y"
    api = BaseHook.get_connection('stock_api')
    response = requests.get(url, headers=api.extra_dejson['headers'])
    return json.dumps(response.json()['chart']['result'][0])

#guardar los datos en la bd minio
def _store_stock_prices(stock):
    
    #crear el cliente de minio
    client = _get_minio_client()
    #creae bucket name
    bucket_name = 'stock-market'
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)
    stock = json.loads(stock)
    symbol = stock['meta']['symbol']
    data = json.dumps(stock,ensure_ascii=False).encode('utf8')
    objw = client.put_object(
            bucket_name=bucket_name,
            object_name = f'{symbol}/prices.json',
            data=BytesIO(data),
            length=len(data)
    )
    return f'{objw.bucket_name}/{symbol}'

#esta funcion toma el archivo csv de minio
def _get_formatted_csv(path):
    client = _get_minio_client()
    prefix_name = f"{path.split('/')[1]}/formatted_prices/"
    objects = client.list_objects(BUCKET_NAME, prefix=prefix_name, recursive=True)
    for obj in objects:
        if obj.object_name.endswith('.csv'):
            return obj.object_name
    return AirflowNotFoundException('The csv file does not exist')