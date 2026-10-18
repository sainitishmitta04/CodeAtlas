import json


def clean(data):
    return json.dumps(data)


def run():
    return clean({"a": 1})
