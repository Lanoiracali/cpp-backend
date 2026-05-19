#!/usr/bin/env python3
"""Verify database connectivity and report which backend is in use."""
from __future__ import annotations

import sys

from db_adapter import DATABASE_URL, _USE_PG, get_connection


def main() -> int:
    if not DATABASE_URL:
        print("FAIL: DATABASE_URL is not set (check .env)")
        return 1

    safe = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    print(f"DATABASE_URL target: ...@{safe}")
    print(f"Postgres mode: {_USE_PG}")

    if not _USE_PG:
        print("WARN: DATABASE_URL is set but Postgres adapter is not active.")
        print("      Install psycopg2-binary or fix DATABASE_URL scheme (postgresql://).")

    try:
        conn = get_connection()
    except Exception as exc:
        print(f"FAIL: Could not connect — {exc}")
        return 1

    mode = conn._mode
    print(f"Active connection: {mode}")

    try:
        if mode == "pg":
            row = conn.execute("SELECT version()").fetchone()
            print(f"Server: {row['version'][:80]}...")
            rows = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
                """
            ).fetchall()
            tables = [r["table_name"] for r in rows]
        else:
            row = conn.execute("SELECT sqlite_version()").fetchone()
            print(f"Server: SQLite {row[0]}")
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            tables = [r["name"] for r in rows]

        print(f"Tables ({len(tables)}): {', '.join(tables) or '(none)'}")

        for name in ("users", "record", "students", "sections", "user_tokens"):
            if name in tables:
                count = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
                print(f"  {name}: {count['n']} rows")

        required = {"users", "record"}
        missing = required - set(tables)
        if missing:
            print(f"WARN: Missing core tables: {', '.join(sorted(missing))}")
            print("      Run: python migrate_sqlite_to_pg.py --sqlite-file cppstudrecord_db.sqlite")
            print("      Then: python migrate_part2_pg.py")
            return 2

        print("OK: Database is reachable and core tables exist.")
        return 0
    except Exception as exc:
        print(f"FAIL: Query error — {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
