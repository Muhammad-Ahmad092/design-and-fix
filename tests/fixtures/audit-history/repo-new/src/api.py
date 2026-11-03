def list_all():
    rows = db.query("SELECT * FROM items")
    return rows


def export():
    data = db.query("SELECT * FROM big")
    return data
