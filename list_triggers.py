import sys
from db_adapter import get_connection

def main():
    conn = get_connection()
    try:
        print("Triggers in the database:")
        rows = conn.execute("""
            SELECT trigger_name, event_object_table, action_statement 
            FROM information_schema.triggers;
        """).fetchall()
        for r in rows:
            print(f"Trigger: {r[0]} | Table: {r[1]} | Action: {r[2]}")
            
        print("\nAll User Triggers (via pg_trigger):")
        rows2 = conn.execute("""
            SELECT t.tgname, c.relname, tgtype
            FROM pg_trigger t
            JOIN pg_class c ON t.tgrelid = c.oid
            WHERE NOT t.tgisinternal;
        """).fetchall()
        for r in rows2:
            print(f"Trigger: {r[0]} | Table: {r[1]} | Type: {r[2]}")
    except Exception as e:
        print("Error:", e)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
