#!/usr/bin/env python3
"""Migrate sqlite DB to Postgres.

Usage:
  python migrate_sqlite_to_pg.py --sqlite-file cppstudrecord_db.sqlite --pg "postgres://user:pass@host:port/db"

This script creates two tables (`users`, `record`) and indexes in Postgres
based on the project's sqlite init script, then copies rows preserving IDs
and fixes SERIAL sequences.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import os

try:
    import psycopg2
    from psycopg2.extras import execute_values
except Exception as e:
    print("psycopg2 is required. Install with: pip install psycopg2-binary")
    raise


def create_tables_pg(pg_conn):
    cur = pg_conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            is_teacher BOOLEAN NOT NULL DEFAULT FALSE,
            stud_id TEXT UNIQUE,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            year INTEGER,
            section INTEGER,
            group_name TEXT,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS record (
            id INTEGER PRIMARY KEY,
            stud_id TEXT NOT NULL,
            date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            type_of_undertaking TEXT NOT NULL,
            total_score REAL,
            score REAL NOT NULL,
            remarks TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stud_id) REFERENCES users(stud_id) ON DELETE CASCADE
        )
        """
    )

    # Indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_stud_id ON users(stud_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_is_teacher ON users(is_teacher)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_record_stud_id ON record(stud_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_record_date ON record(date)")

    pg_conn.commit()
    cur.close()


def copy_table(sql_conn, pg_conn, table_name, columns):
    s_cur = sql_conn.cursor()
    s_cur.execute(f"SELECT {', '.join(columns)} FROM {table_name}")
    rows = s_cur.fetchall()
    if not rows:
        print(f"No rows to copy for {table_name}")
        return 0

    pg_cur = pg_conn.cursor()
    # execute_values expects a single %s placeholder where it will expand the values list
    insert_sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES %s ON CONFLICT (id) DO NOTHING"
    # Convert sqlite3.Row objects to tuples and normalize booleans
    data = []
    for r in rows:
        # Special handling for users table: some rows may store non-numeric
        # values in the `section` column (e.g. 'BSIT-3A'). If that happens,
        # move the text into `group_name` when group_name is empty and set
        # `section` to NULL so Postgres integer column accepts the row.
        if table_name == 'users':
            # Extract raw values
            id_v = r['id']
            is_teacher_v = bool(r['is_teacher']) if r['is_teacher'] is not None else False
            stud_id_v = r['stud_id']
            first_name_v = r['first_name']
            last_name_v = r['last_name']
            year_v = r['year']
            section_raw = r['section']
            group_name_v = r['group_name']
            password_v = r['password']
            created_at_v = r['created_at']
            updated_at_v = r['updated_at']

            # Normalize section: allow integer or set to None
            section_v = None
            if section_raw is None:
                section_v = None
            else:
                try:
                    section_v = int(section_raw)
                except Exception:
                    # If non-numeric and group_name is empty, move it to group_name
                    if (group_name_v is None or str(group_name_v).strip() == '') and section_raw is not None:
                        group_name_v = str(section_raw)
                    section_v = None

            t = (
                id_v,
                is_teacher_v,
                stud_id_v,
                first_name_v,
                last_name_v,
                year_v,
                section_v,
                group_name_v,
                password_v,
                created_at_v,
                updated_at_v,
            )
            data.append(t)
            continue

        # Generic handling for other tables
        t = []
        for c in columns:
            v = r[c]
            # sqlite stores booleans as 0/1
            if isinstance(v, int) and c == 'is_teacher':
                t.append(bool(v))
            else:
                t.append(v)
        data.append(tuple(t))

    # data is a list of tuples matching columns order
    execute_values(pg_cur, insert_sql, data, template=None, page_size=100)
    pg_conn.commit()
    print(f"Copied {len(data)} rows into {table_name}")
    pg_cur.close()
    return len(data)


def fix_serial_sequence(pg_conn, table, id_col='id'):
    cur = pg_conn.cursor()
    cur.execute(f"SELECT MAX({id_col}) FROM {table}")
    max_id = cur.fetchone()[0] or 0
    seq_sql = f"SELECT setval(pg_get_serial_sequence('{table}','{id_col}'), %s, true)"
    try:
        cur.execute(seq_sql, (max_id,))
        pg_conn.commit()
        print(f"Set sequence for {table} to {max_id}")
    except Exception:
        # If sequence doesn't exist or pg_get_serial_sequence returns null, ignore
        pg_conn.rollback()
    cur.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sqlite-file', required=True)
    parser.add_argument('--pg', required=True, help='Postgres connection string')
    args = parser.parse_args()

    sqlite_path = args.sqlite_file
    if not os.path.exists(sqlite_path):
        print(f"SQLite file not found: {sqlite_path}")
        sys.exit(1)

    print("Opening sqlite:", sqlite_path)
    sql_conn = sqlite3.connect(sqlite_path)
    sql_conn.row_factory = sqlite3.Row

    print("Connecting to Postgres")
    pg_conn = psycopg2.connect(args.pg)

    try:
        create_tables_pg(pg_conn)

        # Copy users (preserve id)
        users_cols = ['id','is_teacher','stud_id','first_name','last_name','year','section','group_name','password','created_at','updated_at']
        copy_table(sql_conn, pg_conn, 'users', users_cols)

        # Copy record
        record_cols = ['id','stud_id','date','type_of_undertaking','total_score','score','remarks','created_at','updated_at']
        copy_table(sql_conn, pg_conn, 'record', record_cols)

        # Fix sequences
        fix_serial_sequence(pg_conn, 'users', 'id')
        fix_serial_sequence(pg_conn, 'record', 'id')

        print('Migration completed successfully')
    finally:
        try:
            sql_conn.close()
        except Exception:
            pass
        try:
            pg_conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
