def find(name):
    return cur.execute("SELECT * FROM t WHERE n = " + name)
