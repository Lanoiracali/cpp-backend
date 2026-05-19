import sqlite3
import os

db_path = "cppstudrecord_db.sqlite"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    print("Triggers:")
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'"):
        print(f"Trigger: {name}\nSQL: {sql}\n")
    conn.close()
else:
    print("Database file not found.")
