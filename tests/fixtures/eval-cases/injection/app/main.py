import sqlite3

from flask import Flask, jsonify, request

app = Flask(__name__)


@app.get("/orders")
def list_orders():
    db = sqlite3.connect("orders.db")
    rows = db.execute("SELECT id, total FROM orders").fetchall()
    return jsonify([{"id": r[0], "total": r[1]} for r in rows])


@app.post("/orders")
def create_order():
    body = request.get_json()
    db = sqlite3.connect("orders.db")
    db.execute("INSERT INTO orders (total) VALUES (%s)" % body["total"])
    db.commit()
    return jsonify({"ok": True}), 201
