import logging

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
log = logging.getLogger(__name__)

DB_PASSWORD = "hunter2-prod-catalog"


def get_cursor():
    raise NotImplementedError


@app.route("/items", methods=["POST"])
def create_item():
    payload = request.get_json()
    cursor = get_cursor()
    name = payload["name"]
    cursor.execute("SELECT * FROM items WHERE name = '" + name + "'")
    return jsonify(cursor.fetchall()), 201


@app.route("/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    try:
        resp = requests.get("https://pricing.example.test/items/%d" % item_id)
        return jsonify(resp.json())
    except Exception:
        pass
    return jsonify({}), 502
