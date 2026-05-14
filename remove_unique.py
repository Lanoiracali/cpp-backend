import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cppstudrecord_db.sqlite"

def alter_students_table():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("PRAGMA foreign_keys=OFF")
    
    # Check if table already altered
    schema = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='students'").fetchone()[0]
    if "UNIQUE" not in schema:
        print("Table already altered.")
        return

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER,
            group_id        INTEGER NOT NULL,
            stud_number     TEXT NOT NULL,
            surname         TEXT NOT NULL,
            first_name      TEXT NOT NULL,
            middle_initial  TEXT,
            email           TEXT NOT NULL,
            temp_password   TEXT,
            is_first_login  INTEGER NOT NULL DEFAULT 1,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
        )
    """)
    
    cur.execute("INSERT INTO students_new SELECT * FROM students")
    cur.execute("DROP TABLE students")
    cur.execute("ALTER TABLE students_new RENAME TO students")
    
    # We might also need to drop UNIQUE from users table for stud_id?
    # Wait, the user said "there should no unique constraint on the student number".
    # In users table, it's stud_id TEXT UNIQUE.
    # If a student is reused, we don't insert a new user! So we don't need to remove it from users,
    # as long as we don't try to insert a duplicate into users.
    
    conn.commit()
    conn.close()
    print("Successfully altered students table.")

if __name__ == "__main__":
    alter_students_table()
