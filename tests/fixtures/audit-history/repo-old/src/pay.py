import requests


def charge(body):
    resp = requests.post(PROVIDER_URL, json=body)
    return resp.json()


def settle():
    time.sleep(5)
