def list_all():
    rows = db.query("SELECT * FROM items")
    return rows
