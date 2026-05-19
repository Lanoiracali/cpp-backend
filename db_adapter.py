import os
import sqlite3
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

_USE_PG = False
_PG = None
_PG_EXTRAS = None

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PATH")

if DATABASE_URL and str(DATABASE_URL).startswith("postgres"):
    try:
        import psycopg2 as _PG
        import psycopg2.extras as _PG_EXTRAS
        _USE_PG = True
    except Exception:
        _USE_PG = False


class DBConn:
    def __init__(self, conn, mode="sqlite"):
        self._conn = conn
        self._mode = mode

    def execute(self, sql, params=()):
        if self._mode == "sqlite":
            return self._conn.execute(sql, params)

        # Postgres mode: convert '?' placeholders to %s
        sql_pg = sql.replace("?", "%s")
        cur = self._conn.cursor(cursor_factory=_PG_EXTRAS.RealDictCursor)
        wrapper = _CursorWrapper(cur)

        # If this looks like an INSERT and doesn't already RETURNING, add RETURNING id
        try:
            if sql_pg.strip().lower().startswith("insert") and "returning" not in sql_pg.lower():
                sql_pg = sql_pg + " RETURNING id"

            cur.execute(sql_pg, params)

            # If we added RETURNING, fetch the id so callers can use cur.lastrowid like sqlite
            if sql_pg.strip().lower().startswith("insert"):
                try:
                    row = cur.fetchone()
                    wrapper.lastrowid = row.get("id") if row else None
                except Exception:
                    wrapper.lastrowid = None

            return wrapper
        except Exception:
            cur.close()
            raise

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        try:
            return self._conn.close()
        except Exception:
            pass


class _CursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        return self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)


def get_connection():
    """Return a connection-like object compatible with the existing sqlite usage.

    If `DATABASE_URL` (or `DATABASE_PATH`) points to a Postgres URL, a Postgres
    connection is used under a thin adapter that provides `execute(...).fetchone()`
    semantics and `cursor.lastrowid` for INSERTs. Otherwise a normal sqlite3
    connection is returned (wrapped).
    """
    global DATABASE_URL

    if _USE_PG and DATABASE_URL:
        pg_conn = _PG.connect(DATABASE_URL)
        return DBConn(pg_conn, mode="pg")

    # Fallback to sqlite
    db_path = DATABASE_URL or (os.path.join(os.path.dirname(__file__), "cppstudrecord_db.sqlite"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return DBConn(conn, mode="sqlite")
