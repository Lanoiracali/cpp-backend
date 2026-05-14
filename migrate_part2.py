"""
Migration: Part 2 schema — sections, groups, students tables.
- Creates sections, groups, students tables
- Adds student_id column to record
- Migrates existing record rows by matching stud_id → students (if any exist)

Run: python migrate_part2.py
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cppstudrecord_db.sqlite"


def migrate(db_path: Path):
    print(f"\n[*] Migrating: {db_path}")
    if not db_path.exists():
        print("  [!] DB not found, skipping.")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # allow schema changes
    cur = conn.cursor()

    # ── 1. sections ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id  INTEGER NOT NULL,
            name        TEXT NOT NULL,
            school_year TEXT,
            semester    TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    print("  [+] Table: sections")

    # ── 2. groups ────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id   INTEGER NOT NULL,
            group_number INTEGER NOT NULL,
            group_name   TEXT NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE
        )
    """)
    print("  [+] Table: groups")

    # ── 3. students ──────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
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
    print("  [+] Table: students")

    # ── 4. Add student_id to record ──────────────────────────────────────────
    existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(record)").fetchall()]
    if "student_id" not in existing_cols:
        cur.execute("ALTER TABLE record ADD COLUMN student_id INTEGER REFERENCES students(id) ON DELETE CASCADE")
        print("  [+] Column: record.student_id")
    else:
        print("  [i] Column record.student_id already exists")

    # ── 5. is_first_login on users ───────────────────────────────────────────
    user_cols = [row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "is_first_login" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_first_login INTEGER NOT NULL DEFAULT 0")
        print("  [+] Column: users.is_first_login")
    else:
        print("  [i] Column users.is_first_login already exists")

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
    print(f"  [+] Done: {db_path}")


if __name__ == "__main__":
    migrate(DB_PATH)
    print("\n[+] Part 2 migration complete.")
